"""Tests for the accelerator renderer. Run: python3 -m pytest dbt-parity-accelerator/tests -q
(or: python3 dbt-parity-accelerator/tests/test_render.py)"""
from __future__ import annotations

import copy
import re
import sys
from pathlib import Path

import yaml

ACC = Path(__file__).resolve().parents[1]
REPO = ACC.parent
sys.path.insert(0, str(ACC))
import render  # noqa: E402

DEMO = ACC / "examples" / "jaffle_shop_demo.yml"
FIXTURES = ACC / "tests" / "fixtures"
# Golden tests compare against the jaffle_shop demo repo this accelerator came from. They run
# only when the accelerator sits inside that repo; standalone copies skip them.
HAS_DEMO = (REPO / "snowflake" / "cicd_setup.sql").exists()


class Skip(Exception):
    pass


def _needs_demo():
    if not HAS_DEMO:
        raise Skip("demo repo not present")


def _render_dict(m: dict) -> dict[str, str]:
    p = ACC / "tests" / ".tmp_manifest.yml"
    p.write_text(yaml.safe_dump(m))
    try:
        return render.render(p)
    finally:
        p.unlink()


def _snow_cmds(workflow_text: str) -> list[str]:
    """All `snow ...` commands, line continuations joined, whitespace collapsed."""
    text = re.sub(r"\\\n\s*", " ", workflow_text)
    cmds = [re.sub(r"\s+", " ", l.strip()) for l in text.splitlines() if l.strip().startswith("snow ")]
    # Intentional change: the demo passed the target name to --env; templates pass the env.yml name.
    return [c.replace("steps.ctx.outputs.env", "steps.ctx.outputs.target") for c in cmds]


def _statements(sql: str) -> set[str]:
    body = "\n".join(l.split("--", 1)[0] for l in sql.splitlines())
    body = re.sub(r"COMMENT\s*=\s*'[^']*'", "", body, flags=re.I)  # wording may differ
    return {re.sub(r"\s+", " ", s).strip().upper() for s in body.split(";") if s.strip()}


# ---- golden: the demo manifest reproduces the demo repo ----------------------------

def test_golden_dbt_files():
    _needs_demo()
    out = render.render(DEMO)
    for f in ("jaffle_shop/dbt_projects_profiles.yml", "jaffle_shop/env.yml"):
        assert yaml.safe_load(out[f]) == yaml.safe_load((REPO / f).read_text()), f


def test_golden_workflows():
    _needs_demo()
    out = render.render(DEMO)
    for f in ("dbt-cd.yml", "dbt-ci.yml", "dbt-ci-cleanup.yml"):
        rel = f".github/workflows/{f}"
        got, want = yaml.safe_load(out[rel]), yaml.safe_load((REPO / rel).read_text())
        for key in (True, "permissions", "concurrency", "env", "name"):  # `on:` parses as True
            assert got.get(key) == want.get(key), (rel, key)
        assert _snow_cmds(out[rel]) == _snow_cmds((REPO / rel).read_text()), rel


def test_golden_sql_covers_demo():
    _needs_demo()
    out = render.render(DEMO)
    rendered = set().union(*(_statements(t) for p, t in out.items() if p.endswith(".sql") and "rollback" not in p))
    demo = _statements((REPO / "snowflake" / "cicd_setup.sql").read_text())
    missing = {s for s in demo if not s.startswith("USE ROLE")} - rendered
    assert not missing, f"demo statements not produced: {sorted(missing)}"


# ---- safety and determinism ----------------------------------------------------------

def test_deterministic():
    assert render.render(DEMO) == render.render(DEMO)


def test_no_forbidden_patterns_in_any_fixture():
    for fx in [DEMO, *sorted(FIXTURES.glob("*.yml"))]:
        for path, text in render.render(fx).items():
            for line in text.splitlines():
                s = line.strip()
                if s.startswith(("#", "--")):
                    continue
                assert "--force" not in s, (fx.name, path)
                assert "CREATE OR REPLACE" not in s.upper(), (fx.name, path)
                if path.endswith(".sql") and "rollback" not in path:
                    assert not s.upper().startswith("DROP"), (fx.name, path)


def test_adopted_objects_not_created():
    out = render.render(DEMO)
    sql = out["snowflake/00_roles.sql"] + out["snowflake/10_objects.sql"]
    assert "CREATE ROLE IF NOT EXISTS DBT_DEV_ROLE" not in sql
    assert "CREATE DATABASE" not in sql
    assert "CREATE WAREHOUSE" not in sql


def test_rejects_injection():
    m = yaml.safe_load(DEMO.read_text())
    m["environments"][0]["database"]["name"] = "DBT_DEV; DROP DATABASE X"
    try:
        _render_dict(m)
    except render.ManifestError:
        return
    raise AssertionError("injection accepted")


def test_rejects_env_target_collision():
    m = yaml.safe_load(DEMO.read_text())
    m["environments"][0]["env_yml_env"] = "qa"     # dev's --env name == qa's --target name
    m["environments"][1]["env_yml_env"] = "qa_env"
    try:
        _render_dict(m)
    except render.ManifestError as e:
        assert "--env and --target" in str(e)
        return
    raise AssertionError("collision accepted")


def test_rejects_azure_oidc():
    m = yaml.safe_load(DEMO.read_text())
    m["git"]["provider"] = "azure"
    try:
        _render_dict(m)
    except render.ManifestError:
        return
    raise AssertionError("azure+oidc accepted")


def test_rejects_service_user_collision():
    m = yaml.safe_load((FIXTURES / "live_brownfield_acp.yml").read_text())
    m["service_user_prefix"] = "SVC_GH_DBT_"   # the jaffle_shop pipeline's users
    try:
        _render_dict(m)
    except render.ManifestError as e:
        assert "already exist" in str(e)
        return
    raise AssertionError("service user collision accepted")


def test_live_brownfield_reuses_existing_object():
    out = render.render(FIXTURES / "live_brownfield_acp.yml")
    cd = out[".github/workflows/dbt-cd.yml"]
    assert "snow dbt deploy ACP_DBT " in cd and "--schema DBT" in cd
    assert "CREATE DATABASE" not in out["snowflake/10_objects.sql"]
    assert "CREATE ROLE IF NOT EXISTS ACP_DBT_STAGE_ROLE" in out["snowflake/00_roles.sql"]
    assert "SVC_GH_ACP_DBT_STAGE" in out["snowflake/30_service_users.sql"]


def test_n_environments():
    m = yaml.safe_load(DEMO.read_text())
    uat = copy.deepcopy(m["environments"][1])
    uat.update(name="uat", branch="uat", target="uat", env_yml_env="uat", hosts_ci=False)
    uat["database"] = {"name": "DBT_UAT", "adopt": False}
    uat["role"] = {"name": "DBT_UAT_ROLE", "adopt": False}
    m["environments"].insert(2, uat)
    out = _render_dict(m)
    assert "CREATE DATABASE IF NOT EXISTS DBT_UAT" in out["snowflake/10_objects.sql"]
    assert "uat) db=DBT_UAT" in out[".github/workflows/dbt-cd.yml"]
    assert "ref:refs/heads/uat" in out["snowflake/30_service_users.sql"]
    assert "uat" in yaml.safe_load(out["jaffle_shop/dbt_projects_profiles.yml"])["jaffle_shop"]["outputs"]


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"PASS {name}")
            except Skip as s:
                print(f"SKIP {name}: {s}")
            except Exception as exc:  # noqa: BLE001
                fails += 1; print(f"FAIL {name}: {exc}")
    sys.exit(1 if fails else 0)
