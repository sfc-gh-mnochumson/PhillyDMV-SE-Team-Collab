# Sandbox validation (2026-11-18)

Live run of the skill's workflow in `SFSENORTHAMERICA-JDAMARO_AWS1`, using synthetic customers.
All objects are prefixed `SYN_` and removed afterwards with the generated `rollback.sql`.

## Brownfield (`SYN_BF_*`) with a GitHub pipeline
Seed (the customer's existing setup): 4 roles (dev/qa/prod engineer, analyst), `SYN_BF_DEV`/`SYN_BF_PROD`
owned by SYSADMIN, `SYN_BF_QA` owned by ACCOUNTADMIN, and an existing service user `SVC_SYN_BF_DBT_DEV`.
Repo: private `sfc-gh-jdamaro/dbt-parity-sandbox`, jaffle_shop without profile or env.yml.

| Check | Result |
|---|---|
| Read-only discovery -> propose -> manifest | Flagged the ACCOUNTADMIN-owned database |
| Colliding service-user prefix | Rejected by render.py (`service users already exist`) |
| Batches 00/10/20/30 | 68 statements, no errors; adopted roles and databases not recreated |
| First CD on dev (OIDC) | Passed: deploy + build |
| PR dev->qa, no qa state | Full-build fallback, passed; merge -> qa CD passed; cleanup dropped `CI_PR_1` schema and object |
| Feature PR into dev with state | Deferred; built `dim_parts` only (PASS=3 of 12 models + 22 tests) |
| PR dev->qa with state | Deferred to the qa object, PASS=3 |
| PR qa->main, first prod | Full-build fallback PASS=34; merge -> prod CD passed |
| Analyst role on prod, secondary roles NONE | SELECT works; CREATE TABLE refused |

## Greenfield (`SYN_GF_*`), local only
Everything created: 4 roles, warehouse, 2 databases, 3 schemas, 3 OIDC users, 30 grants.
`snow dbt deploy` + `build` as the dev role: PASS=34. The run used a human user, so secondary
roles may have hidden a missing grant; the brownfield pipeline (one role per service user) is the
stronger proof for the same grant templates.

## Not covered
GitLab and Azure DevOps pipelines (still pilot), packages/EAI batch 50, the scheduled task batch 60.
