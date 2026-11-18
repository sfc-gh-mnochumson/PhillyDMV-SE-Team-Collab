# Apply: approval-gated batches

Rendered SQL lives in `<staging>/snowflake/`. Batches run strictly in order, **one approval per
batch**. Before each batch: say in plain language what it does, list objects created vs
adopted, and show the SQL. After each batch: show the result and stop on the first error.

| Batch | File | Run as | Contents |
|---|---|---|---|
| 00 | `00_roles.sql` | `admin_roles.security` | Create missing roles, grant them to `admin_roles.objects` |
| 10 | `10_objects.sql` | each object's owner role | Warehouse, databases, object schema, CI schema |
| 20 | `20_grants.sql` | `admin_roles.security` (+ share owner role) | Source reads, env build rights, read roles, CI read-only |
| 30 | `30_service_users.sql` | `admin_roles.security` | One SERVICE user per environment + CI, OIDC or key pair |
| 50 | `50_account_level.sql` | ACCOUNTADMIN | Network rule + EAI for dbt packages. **Separate approval**, account-level |
| 60 | `60_schedule.sql` | prod build role | Scheduled task, created suspended (run after Verify step 2) |
| -- | `rollback.sql` | never run by the agent | Drops only what was created, for the user to review and run |

## How to run a batch
- Run statements **one at a time** with `sql_execute` (it takes a single statement). Keep the
  `USE ROLE` lines: run each one before the statements that follow it.
- Everything is additive and idempotent (`IF NOT EXISTS`, `GRANT`), so re-running a batch after a
  fix is safe.
- If a statement fails:
  - `insufficient privileges` -> the role in the batch can't do it. Report which role and which
    object, and offer the `access-troubleshooter` skill. Do not switch to ACCOUNTADMIN on your own.
  - Object owned by another role (often ACCOUNTADMIN-owned databases) -> set
    `environments[].database.owner_role` in the manifest, re-render, re-run batch 10.
  - Anything else -> stop and show the error.
- `sql_execute` only runs on the active connection. Confirm it is the customer's account (D1)
  before the first batch.

## Export mode (someone else runs the SQL)
Give the user the batch files and this table as the runbook. Tell them to run batches in order and
send back any error text. Resume at Verify once they confirm.

## Repo files
Copy the reviewed files into the repo:
- `<project_dir>/dbt_projects_profiles.yml`, `<project_dir>/env.yml`
- GitHub: `.github/workflows/dbt-{ci,cd,ci-cleanup}.yml`
- GitLab: `.gitlab-ci.yml` + `ci/dbt_pipeline.sh`
- Azure: `azure-pipelines.yml` + `ci/dbt_pipeline.sh`
- `snowflake/*.sql` (kept for audit, including `rollback.sql`) and `deployment.yml`

If the project had an in-project `profiles.yml`, follow the PROFILES policy of
`dbt-projects-on-snowflake` before adding `dbt_projects_profiles.yml`. Never delete the user's file.
Committing and pushing: only when the user asks (branch -> PR into the lowest environment is the
natural first test, see verify.md).
