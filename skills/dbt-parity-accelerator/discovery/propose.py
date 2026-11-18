#!/usr/bin/env python3
"""Turn discovery results into interview defaults and warnings. Read-only, offline.

    python3 propose.py <account.json> <scan.json>

account.json holds the rows of the discovery probes (see discovery.sql), keyed by probe:
  {"account": "...", "dbt_projects": [...SHOW DBT PROJECTS rows...], "databases": [...],
   "roles": [...], "users": [...], "warehouses": [...], "integrations": [...],
   "manage_grants_roles": ["SECURITYADMIN", ...]}
scan.json is the output of scan_repo.py.

Prints {"flags": [...], "draft": {...partial deployment.yml...}}. The draft is a starting
point the interview confirms; nothing here is applied. Only structured fields are read.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ENV_HINTS = [("dev", re.compile(r"(^|_)(DEV|DEVELOPMENT)$")),
             ("qa", re.compile(r"(^|_)(QA|TEST|UAT)$")),
             ("stage", re.compile(r"(^|_)(STAGE|STAGING|STG)$")),
             ("prod", re.compile(r"(^|_)(PROD|PRD|PRODUCTION)$"))]
LARGE_PROJECT = 200


def _u(v) -> str:
    return str(v or "").upper()


def _flag(flags, code, severity, message, **data):
    flags.append({"code": code, "severity": severity, "message": message, **data})


def propose(account: dict, scan: dict, project_dir: str | None = None) -> dict:
    flags: list[dict] = []
    projects = scan.get("dbt_projects") or []
    if not projects:
        _flag(flags, "no_dbt_project", "blocker",
              "No dbt_project.yml found: run dbt-projects-on-snowflake ONBOARD first")
        return {"flags": flags, "draft": {}}
    if len(projects) > 1 and not project_dir:
        _flag(flags, "monorepo", "question",
              "Several dbt projects found: one manifest per project",
              projects=[p["project_dir"] for p in projects])
    proj = next((p for p in projects if p["project_dir"] == project_dir), projects[0])

    # --- dbt project and existing objects
    pf = proj["profile_files"]
    if pf.get("profiles.yml") and not pf.get("dbt_projects_profiles.yml"):
        _flag(flags, "in_project_profiles_yml", "question",
              "In-project profiles.yml: follow dbt-projects-on-snowflake PROFILES rename policy")
    if proj["env_vars_missing_dbt_prefix"]:
        _flag(flags, "env_var_missing_dbt_prefix", "warning",
              "env_var() keys without DBT_ prefix won't resolve on Snowflake; rename to DBT_<UPPER> (never var())",
              keys=proj["env_vars_missing_dbt_prefix"])
    if proj["needs_eai"]:
        _flag(flags, "packages_need_eai", "approval",
              "packages.yml found: needs an account-level external access integration (batch 50)")
    if proj["model_count"] > LARGE_PROJECT or proj["incremental_model_count"]:
        _flag(flags, "large_or_incremental", "info",
              "Large project or incremental models: plan the first deferral state (verify.md step 3)",
              models=proj["model_count"], incremental=proj["incremental_model_count"])
    existing_ci = scan.get("existing_ci") or {}
    if existing_ci.get("github") or existing_ci.get("gitlab") or existing_ci.get("azure"):
        _flag(flags, "existing_ci_files", "approval", "Existing CI files would be replaced or merged",
              files=existing_ci)

    objs = account.get("dbt_projects") or []
    obj_names = sorted({_u(o["name"]) for o in objs if not _u(o["name"]).endswith(("_CI",)) and "_CI_PR_" not in _u(o["name"])})
    runtimes = sorted({o.get("dbt_version") for o in objs if o.get("dbt_version")})
    if len(obj_names) > 1:
        _flag(flags, "several_dbt_objects", "question", "Several dbt project objects exist; which is this project?",
              names=obj_names)
    object_name = obj_names[0] if len(obj_names) == 1 else _u(proj["name"])
    object_schema = next((_u(o["schema_name"]) for o in objs if _u(o["name"]) == object_name), "ANALYTICS")
    if objs:
        _flag(flags, "reuse_dbt_object", "info",
              f"Reuse existing object name {object_name}: a new name would create a second object",
              runtimes=runtimes)

    # --- environments from database names
    dbs = account.get("databases") or []
    envs = []
    for env, pat in ENV_HINTS:
        for d in dbs:
            if pat.search(_u(d["name"])) and _u(d.get("kind")) != "IMPORTED DATABASE":
                envs.append({"name": env, "database": {"name": _u(d["name"]), "adopt": True,
                                                       "owner_role": _u(d["owner"])}})
                if _u(d["owner"]) == "ACCOUNTADMIN":
                    _flag(flags, "db_owned_by_accountadmin", "warning",
                          f"{d['name']} is owned by ACCOUNTADMIN: batch 10 needs ACCOUNTADMIN for it",
                          database=_u(d["name"]))
                break
    if not envs:
        _flag(flags, "greenfield_environments", "question",
              "No environment databases found: recommend creating dev, qa, prod")

    # --- roles: adopt candidates per environment
    roles = {_u(r["name"]) for r in account.get("roles") or []}
    for e in envs:
        base = e["database"]["name"]
        cand = [r for r in sorted(roles) if base in r or (e["name"].upper() in r and "DBT" in r)]
        e["role_candidates"] = cand

    # --- shared/imported sources
    shared = [_u(d["name"]) for d in dbs if _u(d.get("kind")) == "IMPORTED DATABASE"]
    src_all = [s.upper() for s in proj.get("source_databases") or []]
    src = [s for s in src_all if re.match(r"^[A-Z_][A-Z0-9_$]*$", s)]
    if len(src) != len(src_all):
        _flag(flags, "dynamic_source_database", "question",
              "Some sources set database with an expression (e.g. env_var()); ask which database each environment reads")
    sources = [{"database": s, "shared": True, "owner_role": next(_u(d["owner"]) for d in dbs if _u(d["name"]) == s)}
               if s in shared else {"database": s, "schemas": []} for s in src]
    missing_src = [s for s in src if s not in {_u(d["name"]) for d in dbs}]
    if missing_src:
        _flag(flags, "source_db_not_found", "warning", "Source databases referenced in YAML not visible to this role",
              databases=missing_src)

    # --- service users and admin rights
    users = sorted(_u(u["name"]) for u in account.get("users") or [] if _u(u.get("type")) == "SERVICE")
    prefix = "SVC_DBT_"
    if any(u.startswith(prefix) for u in users):
        prefix = f"SVC_{object_name}_"
        _flag(flags, "service_user_prefix_taken", "warning",
              f"Existing service users use SVC_DBT_*; proposing prefix {prefix}", existing=users)
    mg = {_u(r) for r in account.get("manage_grants_roles") or []}
    security = "SECURITYADMIN"
    if security not in mg:
        _flag(flags, "security_role_lacks_manage_grants", "blocker",
              "No MANAGE GRANTS seen for SECURITYADMIN: grants on objects it doesn't own will fail",
              roles_with_manage_grants=sorted(mg))

    whs = [_u(w["name"]) for w in account.get("warehouses") or []]
    wh = next((w for w in whs if "DBT" in w or "TRANSFORM" in w), None)
    eai = next((_u(i["name"]) for i in account.get("integrations") or []
                if _u(i.get("type")).startswith("EXTERNAL_ACCESS")), None)

    draft = {
        "version": 1,
        "account": account.get("account"),
        "dbt": {"project_dir": proj["project_dir"], "profile_name": proj["profile"],
                "object_name": object_name, "object_schema": object_schema,
                "runtime": runtimes[0] if len(runtimes) == 1 else "detected"},
        "warehouse": {"name": wh or "DBT_WH", "adopt": bool(wh)},
        "admin_roles": {"objects": "SYSADMIN", "security": security},
        "environments": envs,
        "service_user_prefix": prefix,
        "discovered": {"service_users": users, "profile_targets": proj["profile_targets"],
                       "env_yml_environments": proj["env_yml_environments"]},
        "sources": sources,
        "packages": {"needs_eai": proj["needs_eai"], **({"eai_name": eai, "eai_exists": True} if eai else {})},
    }
    return {"flags": flags, "draft": draft}


def _key(item):
    if isinstance(item, dict):
        return item.get("name") or item.get("database")
    return None


def merge(draft, answers):
    """Interview answers over the discovery draft. Dicts merge deeply; lists of named dicts
    (environments, sources) merge by name/database, keeping the answers' order first."""
    if isinstance(draft, dict) and isinstance(answers, dict):
        out = dict(draft)
        for k, v in answers.items():
            out[k] = merge(draft.get(k), v) if k in draft else v
        return out
    if isinstance(draft, list) and isinstance(answers, list) and all(_key(x) for x in draft + answers):
        by = {_key(x): x for x in draft}
        merged = [merge(by.pop(_key(a), {}), a) for a in answers]
        return merged + list(by.values())
    return answers


def to_manifest(draft: dict, answers: dict) -> dict:
    m = merge(draft, answers)
    for e in m.get("environments") or []:
        e.pop("role_candidates", None)   # discovery hint, not a manifest field
    return m


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    acct = json.loads(Path(sys.argv[1]).read_text())
    scn = json.loads(Path(sys.argv[2]).read_text())
    print(json.dumps(propose(acct, scn), indent=2))
