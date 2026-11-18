"""End-to-end offline tests on synthetic customers: real scan -> propose -> answers -> render.
Run: python3 dbt-parity-accelerator/tests/test_synthetic.py"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

ACC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ACC))
sys.path.insert(0, str(ACC / "discovery"))
import propose  # noqa: E402
import render  # noqa: E402
import scan_repo  # noqa: E402

SYN = ACC / "tests" / "synthetic"


def run(scenario: str):
    d = SYN / scenario
    scan = scan_repo.scan(d / "repo")
    account = json.loads((d / "account.json").read_text())
    prop = propose.propose(account, scan)
    manifest = propose.to_manifest(prop["draft"], yaml.safe_load((d / "answers.yml").read_text()))
    tmp = d / ".manifest.tmp.yml"
    tmp.write_text(yaml.safe_dump(manifest, sort_keys=False))
    try:
        files = render.render(tmp)
    finally:
        tmp.unlink()
    return scan, prop, manifest, files


def codes(prop):
    return {f["code"] for f in prop["flags"]}


# ---- greenfield ----------------------------------------------------------------------

def test_greenfield_creates_everything():
    scan, prop, m, files = run("greenfield_northwind")
    assert "greenfield_environments" in codes(prop)
    assert "reuse_dbt_object" not in codes(prop)
    objs = files["snowflake/10_objects.sql"]
    for db in ("NW_DEV", "NW_QA", "NW_PROD"):
        assert f"CREATE DATABASE IF NOT EXISTS {db}" in objs
    assert "CREATE WAREHOUSE IF NOT EXISTS NW_DBT_WH" in objs
    roles = files["snowflake/00_roles.sql"]
    for r in ("NW_DBT_DEV_ROLE", "NW_DBT_QA_ROLE", "NW_DBT_PROD_ROLE", "NW_PROD_READER", "NW_DBT_CI_ROLE"):
        assert f"CREATE ROLE IF NOT EXISTS {r}" in roles
    users = files["snowflake/30_service_users.sql"]
    assert "SUBJECT = 'repo:northwind/analytics:ref:refs/heads/main'" in users
    assert "SUBJECT = 'repo:northwind/analytics:pull_request'" in users
    assert "GRANT SELECT ON FUTURE TABLES IN SCHEMA NW_RAW.CRM TO ROLE NW_DBT_CI_ROLE" in files["snowflake/20_grants.sql"]
    assert "snowflake/50_account_level.sql" not in files          # no packages
    prof = yaml.safe_load(files["northwind/dbt_projects_profiles.yml"])["northwind"]["outputs"]
    assert set(prof) == {"dev", "qa", "prod", "ci_dev", "ci_qa"}
    rb = files["snowflake/rollback.sql"]
    assert "DROP DATABASE IF EXISTS NW_PROD" in rb and "DROP WAREHOUSE IF EXISTS NW_DBT_WH" in rb


# ---- brownfield ----------------------------------------------------------------------

def test_brownfield_flags_every_trap():
    scan, prop, m, files = run("brownfield_contoso")
    c = codes(prop)
    expected = {"in_project_profiles_yml", "env_var_missing_dbt_prefix", "packages_need_eai",
                "large_or_incremental", "existing_ci_files", "reuse_dbt_object",
                "db_owned_by_accountadmin", "service_user_prefix_taken", "dynamic_source_database"}
    assert expected <= c, expected - c
    keys = next(f for f in prop["flags"] if f["code"] == "env_var_missing_dbt_prefix")["keys"]
    assert keys == ["ERP_DATABASE", "SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER"]


def test_brownfield_adopts_and_never_recreates():
    scan, prop, m, files = run("brownfield_contoso")
    objs = files["snowflake/10_objects.sql"]
    assert "CREATE DATABASE" not in objs and "CREATE WAREHOUSE" not in objs
    assert "USE ROLE ACCOUNTADMIN;\nCREATE SCHEMA IF NOT EXISTS EDW_QA.DBT" in objs   # owner-role routing
    for adopted in ("EDW_DEV_ENGINEER", "EDW_QA_ENGINEER", "EDW_PROD_ENGINEER", "ANALYST"):
        assert f"CREATE ROLE IF NOT EXISTS {adopted}" not in files["snowflake/00_roles.sql"]
    assert "CREATE ROLE IF NOT EXISTS EDW_DBT_CI" in files["snowflake/00_roles.sql"]
    rb = files["snowflake/rollback.sql"]
    assert "DROP DATABASE" not in rb and "DROP ROLE IF EXISTS EDW_DEV_ENGINEER" not in rb


def test_brownfield_reuses_object_runtime_and_eai():
    scan, prop, m, files = run("brownfield_contoso")
    assert m["dbt"]["object_name"] == "CONTOSO_ANALYTICS" and m["dbt"]["runtime"] == "1.10.15"
    cd = files[".github/workflows/dbt-cd.yml"]
    assert "snow dbt deploy CONTOSO_ANALYTICS " in cd and "--schema DBT" in cd
    assert "--external-access-integration DBT_HUB_EAI" in cd
    assert "snowflake/50_account_level.sql" not in files     # EAI exists: no account-level batch


def test_brownfield_service_users_do_not_collide():
    scan, prop, m, files = run("brownfield_contoso")
    users = files["snowflake/30_service_users.sql"]
    assert "SVC_CONTOSO_ANALYTICS_PROD" in users
    assert "CREATE USER IF NOT EXISTS SVC_DBT_PROD" not in users


def test_brownfield_sources_shared_and_owned():
    scan, prop, m, files = run("brownfield_contoso")
    g = files["snowflake/20_grants.sql"]
    assert "GRANT IMPORTED PRIVILEGES ON DATABASE WEATHER_SHARE TO ROLE EDW_DBT_CI" in g
    assert "GRANT USAGE ON SCHEMA RAW_ERP.SAP TO ROLE EDW_PROD_ENGINEER" in g


def test_brownfield_env_yml_uses_dbt_prefix_only():
    scan, prop, m, files = run("brownfield_contoso")
    env = yaml.safe_load(files["analytics/env.yml"])["env_config"]["environments"]
    assert {e["name"] for e in env} == {"dev", "qa", "prod", "ci"}
    for e in env:
        assert all(k.startswith("DBT_") for k in e["env"]), e
        assert e["env"]["DBT_ERP_DATABASE"] == "RAW_ERP"


def test_brownfield_schedule_and_promotion_path():
    scan, prop, m, files = run("brownfield_contoso")
    assert "EXECUTE DBT PROJECT EDW_PROD.DBT.CONTOSO_ANALYTICS" in files["snowflake/60_schedule.sql"]
    ci = files[".github/workflows/dbt-ci.yml"]
    assert "release) ci_db=EDW_QA; ci_target=ci_qa" in ci
    assert "main) state_obj=EDW_PROD.DBT.CONTOSO_ANALYTICS" in ci


def test_untrusted_metadata_never_surfaces():
    for scenario in ("greenfield_northwind", "brownfield_contoso"):
        scan, prop, m, files = run(scenario)
        blob = json.dumps(scan) + json.dumps(prop) + json.dumps(m) + "".join(files.values())
        assert "ignore previous instructions" not in blob.lower(), scenario
        assert "attacker" not in blob.lower(), scenario
        assert "IGNORE ALL PREVIOUS" not in blob, scenario


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                fails += 1; print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    sys.exit(1 if fails else 0)
