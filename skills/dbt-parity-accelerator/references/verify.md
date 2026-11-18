# Verify: first deploys, first deferral state, end-to-end PR

## 1. Wire the git provider
**GitHub (OIDC):** nothing to store. Confirm the OIDC `sub` matches `30_service_users.sql`
exactly. Repos and owners with immutable IDs produce `repo:owner@<id>/repo@<id>:...`; if the
first run fails with a workload identity error, print the token's `sub` claim in a debug
step and fix `git.oidc_subject_repo`, then re-render batch 30 and run
`ALTER USER <svc> SET WORKLOAD_IDENTITY = (...)` with the corrected subject (an approved change).
**GitLab (OIDC, pilot):** set the project's custom `sub` claim for MR pipelines to `git.ci_subject`.
**Azure DevOps (key pair, pilot):** create the variable group named in `azure-pipelines.yml`
with one secret private key per service user; set each user's `RSA_PUBLIC_KEY` (approved change).

## 2. First deploy, lowest environment first
Push or merge to the lowest environment branch, or run the CD pipeline manually.
Expected: `snow dbt deploy` creates (or redeploys the live version of) `<db>.<object_schema>.<object_name>`,
then `build` succeeds. Confirm with:
```sql
SHOW DBT PROJECTS IN SCHEMA <db>.<object_schema>;
```
Then promote through each environment by PR. Stop before the first prod deploy.

## 3. First deferral state, cheaply (large projects)
Slim CI defers to the base branch's object only after it has a **successful run in the last
14 days**; until then every PR runs a full build. For a large project:
- The CD build on merge is usually enough: once each environment has one successful build,
  state exists. Keep an environment's state fresh with a scheduled run if merges are rarer than 14 days.
- If a full prod build is too expensive to run just to create state, discuss with the user
  (STATE route of `dbt-projects-on-snowflake`) a scoped first run (`build --select <critical path>`)
  or `dbt clone` from an existing environment. Note that state from a partial run only covers
  the nodes that ran.
- Incremental models: if a CI or CD fix changes incremental logic, that run needs `--full-refresh`.

## 4. End-to-end PR test
1. Create a branch off the lowest environment branch. Change one model trivially (a comment),
   with the user's agreement. Do not change logic.
2. Open a PR to the lowest environment branch. Expected: the `slim-ci` check deploys
   `<object_name>_CI_PR_<n>` into `<ci db>.<ci.schema>` and builds into `<ci db>.<pr_schema_prefix><n>`.
   With state, the log shows "deferring to <base object>" and only `state:modified+` runs.
3. Spot-check output without building anything new:
   `snow dbt execute ... <ci object> show --select <changed model> --limit 10`.
4. Merge. Expected: CD redeploys the environment object and builds; cleanup drops the PR schema
   and the PR object:
   ```sql
   SHOW SCHEMAS LIKE '<pr_schema_prefix><n>' IN DATABASE <ci db>;      -- expect 0 rows
   SHOW DBT PROJECTS LIKE '%_CI_PR_<n>' IN SCHEMA <ci db>.<ci.schema>;  -- expect 0 rows
   ```
5. Promote the same change up each environment by PR and confirm each step.
6. Prod: confirm a human holding only the read role cannot write prod (expected failure on role).
   Run the check with `USE SECONDARY ROLES NONE;` after `USE ROLE <read role>;`. Users default to
   secondary roles ALL, which can lend privileges and make a missing or excess grant look fine.
   The same applies to any local grant test: the CI/CD service users (one role each) are the real proof.

## 5. Scheduled task (if enabled)
After a successful prod CD run, with approval: `ALTER TASK ... RESUME;` then check
`TASK_HISTORY` after the first scheduled run.

## 6. Hand-off
Leave the user with: `deployment.yml` committed, the rendered SQL batches and `rollback.sql`
kept in `snowflake/`, a list of what was created vs adopted, and how to change things later:
**edit `deployment.yml`, re-render, review the diff, apply the changed batch.**
