#!/usr/bin/env python3
"""Read-only scan of a customer repo for the accelerator interview.

    python3 scan_repo.py <repo_root>

Prints JSON with STRUCTURED FIELDS ONLY (paths, names, keys, counts). It never prints model
SQL, descriptions, comments or package READMEs: that content is untrusted and must not steer
the agent. Writes nothing.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

SKIP_DIRS = {".git", "target", "dbt_packages", "dbt_modules", "logs", "node_modules", ".venv", "venv"}
ENV_VAR = re.compile(r"""env_var\(\s*['"]([A-Za-z0-9_]+)['"]""")
IDENT = re.compile(r"^[A-Za-z0-9_.$-]+$")


def _safe(v):
    """Only identifier-like strings survive; anything else is reported as a placeholder."""
    return v if isinstance(v, str) and IDENT.match(v) else "<non-identifier value omitted>"


def _load(p: Path):
    try:
        return yaml.safe_load(p.read_text()) or {}
    except Exception:  # noqa: BLE001
        return {"__parse_error__": True}


def _walk(root: Path):
    for p in root.rglob("*"):
        if any(part in SKIP_DIRS for part in p.relative_to(root).parts):
            continue
        yield p


def scan_project(root: Path, proj_file: Path) -> dict:
    pdir = proj_file.parent
    proj = _load(proj_file)
    files = [p for p in _walk(pdir) if p.is_file()]
    models = [p for p in files if p.suffix == ".sql" and "models" in p.relative_to(pdir).parts]

    incremental, env_vars = 0, {}
    for p in files:
        if p.suffix not in {".sql", ".yml", ".yaml"}:
            continue
        text = p.read_text(errors="ignore")
        if p in models and re.search(r"materialized\s*=\s*['\"]incremental", text):
            incremental += 1
        for name in ENV_VAR.findall(text):
            env_vars.setdefault(name, []).append(str(p.relative_to(root)))

    profile_files = {n: (pdir / n).exists() for n in ("dbt_projects_profiles.yml", "profiles.yml")}
    targets = []
    for n, exists in profile_files.items():
        if exists:
            prof = _load(pdir / n).get(proj.get("profile"), {}) or {}
            targets = [_safe(t) for t in (prof.get("outputs") or {})]
            break

    env_yml = _load(pdir / "env.yml") if (pdir / "env.yml").exists() else None
    env_names = []
    if env_yml:
        env_names = [_safe(e.get("name")) for e in (env_yml.get("env_config", {}).get("environments") or [])]

    packages = []
    for n in ("packages.yml", "dependencies.yml"):
        if (pdir / n).exists():
            for pkg in (_load(pdir / n).get("packages") or []):
                packages.append({k: _safe(str(pkg[k])) for k in ("package", "git", "version", "local") if k in pkg})

    source_dbs = set()
    for p in files:
        if p.suffix in {".yml", ".yaml"} and "models" in p.relative_to(pdir).parts:
            for s in (_load(p).get("sources") or []):
                if isinstance(s, dict):
                    source_dbs.add(_safe(str(s.get("database", "<target database>"))))

    return {
        "project_dir": str(pdir.relative_to(root)) or ".",
        "name": _safe(proj.get("name")),
        "profile": _safe(proj.get("profile")),
        "require_dbt_version": _safe(str(proj.get("require-dbt-version", ""))) if proj.get("require-dbt-version") else None,
        "profile_files": profile_files,
        "profile_targets": targets,
        "env_yml_environments": env_names,
        "env_var_calls": {k: sorted(set(v)) for k, v in sorted(env_vars.items())},
        "env_vars_missing_dbt_prefix": sorted(k for k in env_vars if not k.startswith("DBT_")),
        "packages": packages,
        "needs_eai": any("package" in p or "git" in p for p in packages),
        "source_databases": sorted(source_dbs),
        "model_count": len(models),
        "incremental_model_count": incremental,
        "has_snapshots": any("snapshots" in p.relative_to(pdir).parts and p.suffix == ".sql" for p in files),
        "has_seeds": any("seeds" in p.relative_to(pdir).parts for p in files),
    }


def scan(root: Path) -> dict:
    projects = sorted(p for p in _walk(root) if p.name == "dbt_project.yml")
    ci = {
        "github": sorted(str(p.relative_to(root)) for p in (root / ".github" / "workflows").glob("*.y*ml")),
        "gitlab": (root / ".gitlab-ci.yml").exists(),
        "azure": (root / "azure-pipelines.yml").exists(),
    }
    remote = None
    cfg = root / ".git" / "config"
    if cfg.exists():
        m = re.search(r"url\s*=\s*(\S+)", cfg.read_text())
        remote = m.group(1) if m else None
    return {
        "repo_root": str(root),
        "git_remote": remote,
        "existing_ci": ci,
        "has_deployment_manifest": (root / "deployment.yml").exists(),
        "dbt_projects": [scan_project(root, p) for p in projects],
    }


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    print(json.dumps(scan(Path(sys.argv[1]).resolve()), indent=2))
