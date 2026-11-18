#!/usr/bin/env python3
"""Render a dbt Projects on Snowflake CI/CD setup from a deployment.yml manifest.

    python3 render.py <deployment.yml> <out_dir>
    python3 render.py <deployment.yml> --check      # validate only

Deterministic: the same manifest always produces the same files. Nothing here talks to
Snowflake or git; applying the output is a separate, approved step (see skill/references/apply.md).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import jinja2
import yaml

HERE = Path(__file__).resolve().parent
TEMPLATES = HERE / "templates"

IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
LOWER_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
ACCOUNT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@/-]*$")
PATH = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]*$")
CRON = re.compile(r"^[0-9*/,\- ]+ [A-Za-z_/]+$")
HOST = re.compile(r"^[A-Za-z0-9.-]+$")
VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")

PROVIDERS = {"github", "gitlab", "azure"}
OIDC_ISSUERS = {
    "github": "https://token.actions.githubusercontent.com",
    "gitlab": "https://gitlab.com",
}
# Patterns that must never appear in generated output (see verify.md).
FORBIDDEN_OUTPUT = [
    # Checked on non-comment lines only, so warnings like "never use --force" are allowed.
    (re.compile(r"^(?!\s*(#|--)).*--force\b", re.M), "--force wipes dbt project run history"),
    (re.compile(r"^(?!\s*(#|--)).*CREATE\s+OR\s+REPLACE", re.I | re.M), "CREATE OR REPLACE is destructive"),
    (re.compile(r"\{\{\s*var\("), "env_var() must not be rewritten to var()"),
]


class ManifestError(ValueError):
    pass


def _req(d: dict, key: str, where: str):
    if key not in d or d[key] in (None, ""):
        raise ManifestError(f"{where}.{key} is required")
    return d[key]


def _check(pattern: re.Pattern, value, where: str) -> str:
    if not isinstance(value, str) or not pattern.match(value):
        raise ManifestError(f"{where} has an invalid value: {value!r}")
    return value


def validate(m: dict) -> None:
    """Fail fast on anything that could produce unsafe SQL/shell or ambiguous dbt runs."""
    if m.get("version") != 1:
        raise ManifestError("version: 1 is required")
    _check(ACCOUNT, _req(m, "account", "manifest"), "account")

    git = _req(m, "git", "manifest")
    provider = _req(git, "provider", "git")
    if provider not in PROVIDERS:
        raise ManifestError(f"git.provider must be one of {sorted(PROVIDERS)}")
    _check(REPO, _req(git, "repo", "git"), "git.repo")
    auth = _req(git, "ci_auth", "git")
    if auth not in {"oidc", "keypair"}:
        raise ManifestError("git.ci_auth must be oidc or keypair")
    if provider == "azure" and auth == "oidc":
        raise ManifestError("git.provider azure requires ci_auth: keypair (OIDC not verified)")
    if auth == "oidc" and provider == "gitlab":
        _check(REPO, _req(git, "ci_subject", "git"), "git.ci_subject")
    if git.get("oidc_subject_repo"):
        _check(REPO, git["oidc_subject_repo"], "git.oidc_subject_repo")
    if git.get("oidc_issuer"):
        if not str(git["oidc_issuer"]).startswith("https://"):
            raise ManifestError("git.oidc_issuer must be an https:// URL")
    _check(VERSION, str(git.get("cli_version", "3.28.0")), "git.cli_version")

    dbt = _req(m, "dbt", "manifest")
    _check(PATH, _req(dbt, "project_dir", "dbt"), "dbt.project_dir")
    if ".." in dbt["project_dir"].split("/"):
        raise ManifestError("dbt.project_dir must stay inside the repo")
    _check(IDENT, _req(dbt, "profile_name", "dbt"), "dbt.profile_name")
    for k in ("object_name", "object_schema"):
        _check(IDENT, _req(dbt, k, "dbt"), f"dbt.{k}")
    for k, per_env in (dbt.get("extra_env_vars") or {}).items():
        if not re.match(r"^DBT_[A-Z0-9_]+$", k):
            raise ManifestError(f"dbt.extra_env_vars key {k!r} must be DBT_<UPPERCASE>")
        if k.startswith("DBT_ENV_SECRET_"):
            raise ManifestError(f"{k}: secrets do not belong in env.yml literals; use a Snowflake SECRET")
        if not isinstance(per_env, dict):
            raise ManifestError(f"dbt.extra_env_vars.{k} must map environment -> value")

    wh = _req(m, "warehouse", "manifest")
    _check(IDENT, _req(wh, "name", "warehouse"), "warehouse.name")
    for k in ("objects", "security"):
        _check(IDENT, _req(_req(m, "admin_roles", "manifest"), k, "admin_roles"), f"admin_roles.{k}")

    envs = _req(m, "environments", "manifest")
    if not isinstance(envs, list) or len(envs) < 1:
        raise ManifestError("environments must be a non-empty list")
    targets, env_names, branches = set(), set(), set()
    for i, e in enumerate(envs):
        w = f"environments[{i}]"
        _check(LOWER_NAME, _req(e, "name", w), f"{w}.name")
        b = _check(BRANCH, _req(e, "branch", w), f"{w}.branch")
        t = _check(LOWER_NAME, _req(e, "target", w), f"{w}.target")
        ev = _check(LOWER_NAME, _req(e, "env_yml_env", w), f"{w}.env_yml_env")
        if b in branches or t in targets or ev in env_names:
            raise ManifestError(f"{w}: branch, target and env_yml_env must be unique across environments")
        branches.add(b); targets.add(t); env_names.add(ev)
        db = _req(e, "database", w)
        _check(IDENT, _req(db, "name", f"{w}.database"), f"{w}.database.name")
        _check(IDENT, db.get("owner_role", m["admin_roles"]["objects"]), f"{w}.database.owner_role")
        _check(IDENT, _req(_req(e, "role", w), "name", f"{w}.role"), f"{w}.role.name")
        if e.get("read_role"):
            _check(IDENT, _req(e["read_role"], "name", f"{w}.read_role"), f"{w}.read_role.name")

    # The ambiguity rule from dbt-projects-on-snowflake: one name, two meanings.
    for e in envs:
        for other in envs:
            if other is not e and e["env_yml_env"] == other["target"]:
                raise ManifestError(
                    f"env.yml environment {e['env_yml_env']!r} (env {e['name']}) equals the profile target of "
                    f"env {other['name']}; --env and --target would select different environments")

    ci = m.get("ci") or {"enabled": False}
    if ci.get("enabled"):
        for k in ("role", "schema", "pr_schema_prefix"):
            _check(IDENT, _req(ci, k, "ci"), f"ci.{k}")
        cienv = _check(LOWER_NAME, _req(ci, "env_yml_env", "ci"), "ci.env_yml_env")
        ci_targets = {f"ci_{e['name']}" for e in envs if e.get("hosts_ci")}
        if not ci_targets:
            raise ManifestError("ci.enabled needs at least one environment with hosts_ci: true")
        if cienv in targets | env_names | ci_targets:
            raise ManifestError(f"ci.env_yml_env {cienv!r} collides with an environment or profile target name")
        if ci_targets & targets:
            raise ManifestError("generated CI target names (ci_<env>) collide with an environment target")
        d = ci.get("default_ci_env")
        if d and d not in {e["name"] for e in envs if e.get("hosts_ci")}:
            raise ManifestError("ci.default_ci_env must name an environment with hosts_ci: true")

    prefix = _check(IDENT, m.get("service_user_prefix", "SVC_DBT_"), "service_user_prefix")
    # CREATE USER IF NOT EXISTS silently skips an existing user, and the GRANT ROLE that follows
    # would then hand this project's role to someone else's pipeline. Refuse any collision.
    taken = {u.upper() for u in (m.get("discovered") or {}).get("service_users", [])}
    planned = {f"{prefix}{e['name'].upper()}" for e in envs} | ({f"{prefix}CI"} if ci.get("enabled") else set())
    clash = sorted(planned & taken)
    if clash:
        raise ManifestError(f"service users already exist: {clash}; choose a different service_user_prefix")

    for i, s in enumerate(m.get("sources") or []):
        w = f"sources[{i}]"
        _check(IDENT, _req(s, "database", w), f"{w}.database")
        if s.get("shared"):
            _check(IDENT, _req(s, "owner_role", w), f"{w}.owner_role")
        else:
            for sch in _req(s, "schemas", w):
                _check(IDENT, sch, f"{w}.schemas")

    pk = m.get("packages") or {}
    if pk.get("needs_eai"):
        _check(IDENT, _req(pk, "eai_name", "packages"), "packages.eai_name")
        if not pk.get("eai_exists"):   # creating it: rule + hosts needed; adopting: name only
            _check(IDENT, _req(pk, "network_rule", "packages"), "packages.network_rule")
            for h in _req(pk, "hosts", "packages"):
                _check(HOST, h, "packages.hosts")

    sc = m.get("schedule") or {}
    if sc.get("enabled"):
        if sc.get("environment") not in {e["name"] for e in envs}:
            raise ManifestError("schedule.environment must name an environment")
        _check(CRON, _req(sc, "cron", "schedule"), "schedule.cron")


def build_context(m: dict) -> dict:
    """Derive everything the templates need, so templates stay logic-light."""
    git = m["git"]
    envs = m["environments"]
    for e in envs:
        e.setdefault("per_user_schema", False)
        e.setdefault("hosts_ci", False)
        e.setdefault("read_role", None)
        e["database"].setdefault("owner_role", m["admin_roles"]["objects"])
        e["database"].setdefault("adopt", False)
        e["role"].setdefault("adopt", False)
        if e["read_role"]:
            e["read_role"].setdefault("adopt", False)
        e["ci_target"] = f"ci_{e['name']}"

    ci = {"enabled": False, "role": "", "schema": "CI", "env_yml_env": "ci",
          "pr_schema_prefix": "CI_PR_", "cleanup": True, **(m.get("ci") or {})}
    ci_envs = [e for e in envs if e["hosts_ci"]] if ci["enabled"] else []
    default_ci_env = None
    if ci_envs:
        name = ci.get("default_ci_env") or ci_envs[0]["name"]
        default_ci_env = next(e for e in ci_envs if e["name"] == name)

    by_branch = {e["branch"]: e for e in envs}
    ci_routes = []
    for base in envs:
        heads = [e for e in ci_envs if e is not default_ci_env]
        build = default_ci_env
        head_label = "<feature>"
        # A PR from an environment branch into the next environment builds in its own DB.
        idx = envs.index(base)
        if idx > 0 and envs[idx - 1] in heads:
            build = envs[idx - 1]
            head_label = envs[idx - 1]["branch"]
        elif idx > 0:
            head_label = envs[idx - 1]["branch"]
        if build is None:
            continue
        ci_routes.append({
            "head_label": head_label, "base": base["branch"], "build_db": build["database"]["name"],
            "state_obj": f"{base['database']['name']}.{m['dbt']['object_schema']}.{m['dbt']['object_name']}",
        })

    provider = git["provider"]
    prefix = m.get("service_user_prefix", "SVC_DBT_")
    subj_repo = git.get("oidc_subject_repo") or git["repo"]

    def subject(branch: str | None) -> str:
        if provider == "github":
            return f"repo:{subj_repo}:" + (f"ref:refs/heads/{branch}" if branch else "pull_request")
        if provider == "gitlab":
            return f"project_path:{subj_repo}:ref_type:branch:ref:{branch}" if branch else git["ci_subject"]
        return ""

    label = {"github": "GitHub Actions", "gitlab": "GitLab CI", "azure": "Azure Pipelines"}[provider]
    env_users = []
    for e in envs:
        comment = f"{label} CD for {e['branch']} branch ({git['ci_auth'].upper()})"
        if e is envs[-1] and len(envs) > 1:
            comment += f"; only non-admin principal with {e['name']} write"
        env_users.append({"name": f"{prefix}{e['name'].upper()}", "role": e["role"]["name"],
                          "env": e["name"], "branch": e["branch"],
                          "subject": subject(e["branch"]), "comment": comment})
    service_users = list(env_users)
    ci_user = None
    if ci["enabled"]:
        ci_user = {"name": f"{prefix}CI", "role": ci["role"], "env": "ci", "branch": None,
                   "subject": subject(None),
                   "comment": f"{label} Slim CI for pull requests ({git['ci_auth'].upper()})"}
        service_users.append(ci_user)

    all_build_roles = [e["role"]["name"] for e in envs] + ([ci["role"]] if ci["enabled"] else [])

    pk = {"needs_eai": False, "eai_name": "DBT_PACKAGES_EAI", "network_rule": "DBT_PACKAGES_NETWORK_RULE",
          "hosts": ["hub.getdbt.com", "codeload.github.com"], **(m.get("packages") or {})}
    sc = {"enabled": False, **(m.get("schedule") or {})}
    sched_env = next((e for e in envs if e["name"] == sc.get("environment")), None)

    dbt = {"threads": 8, "extra_env_vars": {}, **m["dbt"]}
    return {
        **m, "dbt": dbt, "environments": envs, "ci": ci, "ci_envs": ci_envs,
        "default_ci_env": default_ci_env, "ci_routes": ci_routes, "packages": pk,
        "schedule": sc, "sched_env": sched_env, "service_users": service_users,
        "env_users": env_users, "ci_user": ci_user, "all_build_roles": all_build_roles,
        "warehouse": {"adopt": False, "size": "XSMALL", **m["warehouse"]},
        "sources": [{"shared": False, "schemas": [], **s} for s in (m.get("sources") or [])],
        "oidc_issuer": git.get("oidc_issuer") or OIDC_ISSUERS.get(provider, ""),
        "cli_version": str(git.get("cli_version", "3.28.0")),
    }


def outputs(ctx: dict) -> list[tuple[str, str]]:
    """(template, output path) pairs for this manifest. Batch numbers set apply order."""
    pd = ctx["dbt"]["project_dir"]
    out = [
        ("snowflake/00_roles.sql.j2", "snowflake/00_roles.sql"),
        ("snowflake/10_objects.sql.j2", "snowflake/10_objects.sql"),
        ("snowflake/20_grants.sql.j2", "snowflake/20_grants.sql"),
        ("snowflake/30_service_users.sql.j2", "snowflake/30_service_users.sql"),
        ("snowflake/rollback.sql.j2", "snowflake/rollback.sql"),
        ("dbt/dbt_projects_profiles.yml.j2", f"{pd}/dbt_projects_profiles.yml"),
        ("dbt/env.yml.j2", f"{pd}/env.yml"),
    ]
    if ctx["packages"]["needs_eai"] and not ctx["packages"].get("eai_exists"):
        out.append(("snowflake/50_account_level.sql.j2", "snowflake/50_account_level.sql"))
    if ctx["schedule"]["enabled"]:
        out.append(("snowflake/60_schedule.sql.j2", "snowflake/60_schedule.sql"))
    provider = ctx["git"]["provider"]
    if provider == "github":
        out.append(("ci/github/dbt-cd.yml.j2", ".github/workflows/dbt-cd.yml"))
        if ctx["ci"]["enabled"]:
            out.append(("ci/github/dbt-ci.yml.j2", ".github/workflows/dbt-ci.yml"))
            if ctx["ci"]["cleanup"]:
                out.append(("ci/github/dbt-ci-cleanup.yml.j2", ".github/workflows/dbt-ci-cleanup.yml"))
    else:
        out.append(("ci/shared/dbt_pipeline.sh.j2", "ci/dbt_pipeline.sh"))
        out.append(("ci/gitlab/.gitlab-ci.yml.j2", ".gitlab-ci.yml") if provider == "gitlab"
                   else ("ci/azure/azure-pipelines.yml.j2", "azure-pipelines.yml"))
    return out


def make_env() -> jinja2.Environment:
    # [[ ]] / [% %] avoid clashing with GitHub ${{ }} and dbt {{ }} in the rendered files.
    return jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES)),
        variable_start_string="[[", variable_end_string="]]",
        block_start_string="[%", block_end_string="%]",
        comment_start_string="[#", comment_end_string="#]",
        undefined=jinja2.StrictUndefined, keep_trailing_newline=True,
        trim_blocks=False, lstrip_blocks=False, autoescape=False,
    )


def render(manifest_path: str | Path) -> dict[str, str]:
    m = yaml.safe_load(Path(manifest_path).read_text())
    validate(m)
    ctx = build_context(m)
    env = make_env()
    files = {}
    for tpl, dest in outputs(ctx):
        text = env.get_template(tpl).render(**ctx)
        text = re.sub(r"\n{3,}", "\n\n", text).rstrip() + "\n"
        for pat, why in FORBIDDEN_OUTPUT:
            if pat.search(text):
                raise ManifestError(f"{dest}: generated output contains a forbidden pattern ({why})")
        files[dest] = text
    return files


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    try:
        files = render(argv[1])
    except ManifestError as exc:
        print(f"manifest error: {exc}", file=sys.stderr)
        return 1
    if argv[2] == "--check":
        print(f"OK: {len(files)} files would be rendered")
        for p in files:
            print(f"  {p}")
        return 0
    out = Path(argv[2])
    for rel, text in files.items():
        p = out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        if rel.endswith(".sh"):
            p.chmod(0o755)
        print(f"wrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
