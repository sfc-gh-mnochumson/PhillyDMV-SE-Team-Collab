# dbt Parity Accelerator

A Cortex Code (CoCo) skill that sets up **dbt Projects on Snowflake with CI/CD** in a
customer's own Snowflake account and git repo, adopting the roles, databases, warehouses
and dbt project they already have.

The end state:
- one dbt project object per environment (dev -> qa -> prod, or any ordered list)
- promotion by pull request
- **Slim CI**: each PR builds into its own `CI_PR_<n>` schema, only the changed models, and
  defers everything else to the target environment
- automatic cleanup of PR schemas and objects
- one OIDC service user per pipeline identity, each holding exactly one role, so only the
  prod pipeline can write prod

The customer understands and approves every step. Nothing changes in their account or repo
until they approve it.

```
Discover (read-only) -> Interview -> deployment.yml -> Render -> Review -> Apply (gated) -> Verify
```

## How it works

1. **Discover (read-only):** `discovery/discovery.sql` (SHOW and DESCRIBE probes) and
   `discovery/scan_repo.py` record what exists. `discovery/propose.py` turns the findings into
   defaults and flags, e.g. an ACCOUNTADMIN-owned database, `env_var()` keys without the
   `DBT_` prefix, packages that need an external access integration, colliding service users.
   Only structured fields are read; descriptions and comments are treated as untrusted.
2. **Interview:** CoCo asks the questions in `references/interview.md` (git provider, existing
   dbt project, number of environments, adopt or create roles, sources, CI options), with
   defaults from discovery. Each answer goes straight into `deployment.yml`.
3. **Render:** `render.py` validates the manifest and generates everything from it, always
   with the same output: SQL batches, `dbt_projects_profiles.yml`, `env.yml` and pipeline files.
4. **Review:** the customer sees a diff of every file and a plain-language walkthrough of each
   SQL batch.
5. **Apply:** batches run in order, each under its own role, one approval at a time
   (`references/apply.md`). Additive only; `rollback.sql` is generated but never run by CoCo.
6. **Verify:** first deploys, the first Slim CI state, and an end-to-end PR
   (`references/verify.md`).

Steps specific to dbt projects (migrating an existing project, profiles, env.yml, Slim CI
state, scheduling) are handed to CoCo's built-in `dbt-projects-on-snowflake` skill.

## Install

```bash
git clone https://github.com/sfc-gh-jdamaro/dbt-parity-accelerator.git
ln -s "$PWD/dbt-parity-accelerator" ~/.snowflake/cortex/skills/dbt-parity-accelerator
pip install jinja2 pyyaml
```

Then, in Cortex Code with the customer's repo open: **"Set up dbt CI/CD for my project"**.

## Layout

| Path | Purpose |
|---|---|
| `SKILL.md` | The skill: workflow, hard rules, stopping points |
| `references/` | `interview.md`, `apply.md`, `verify.md` |
| `discovery/` | Read-only account probes, repo scanner, `propose.py` |
| `render.py` | Validates `deployment.yml` and renders all files |
| `templates/` | Jinja templates (`[[ ]]` / `[% %]` delimiters, so GitHub `${{ }}` and dbt `{{ }}` pass through) |
| `MANIFEST.md` | `deployment.yml` field reference |
| `examples/jaffle_shop_demo.yml` | A complete manifest (the dev/qa/prod demo it was built from) |
| `tests/` | Renderer, safety and synthetic-customer tests; `SANDBOX_RESULTS.md` |

## Status by provider

| Provider | Auth | Status |
|---|---|---|
| GitHub Actions | OIDC | Validated live: dev -> qa -> prod, Slim CI deferral, cleanup (`tests/SANDBOX_RESULTS.md`) |
| GitLab CI | OIDC | Pilot: rendered and syntax-checked, not yet run end to end |
| Azure DevOps | Key pair | Pilot: rendered and syntax-checked, not yet run end to end |

Not yet run live: the packages integration batch (50) and the scheduled-task batch (60).

## Tests

```bash
python3 tests/test_render.py      # renderer, safety rules, collisions, N environments
python3 tests/test_synthetic.py   # synthetic greenfield + brownfield customers, end to end offline
```

Golden tests against the original demo repo are skipped when it isn't present.

## Safety rules enforced by `render.py`

- No `--force`, `CREATE OR REPLACE`, or `env_var()` rewritten to `var()` in generated output
- Only plain identifiers in the manifest, so manifest values can't inject SQL or shell
- An env.yml environment name may not equal a *different* environment's profile target
- A planned service user may not collide with an existing user (it would silently get this project's role)
