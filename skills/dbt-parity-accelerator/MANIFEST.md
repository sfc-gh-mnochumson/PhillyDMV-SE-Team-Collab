# deployment.yml reference

`deployment.yml` is the single source of truth. The skill fills it in during the interview,
`render.py` validates it and generates every file from it, and it is committed to the
customer repo so the setup can be reviewed, re-run, and audited.
Working example: [examples/jaffle_shop_demo.yml](examples/jaffle_shop_demo.yml).

All Snowflake names must be plain identifiers (`[A-Za-z_][A-Za-z0-9_$]*`); quoted identifiers
are rejected on purpose so manifest values can never inject SQL or shell.

## Top level

| Field | Required | Meaning |
|---|---|---|
| `version` | yes | Always `1` |
| `account` | yes | `ORG-ACCOUNT` identifier the CLI connects to |
| `service_user_prefix` | no | Prefix for CI/CD users, e.g. `SVC_GH_DBT_` -> `SVC_GH_DBT_PROD`, `SVC_GH_DBT_CI` |
| `discovered.service_users` | yes when D9 found any | Existing user names. Rendering fails if a planned user would collide (an existing user would otherwise silently receive this project's role) |

## `git`

| Field | Meaning |
|---|---|
| `provider` | `github`, `gitlab` or `azure` |
| `repo` | `owner/repo` (GitLab: full project path) |
| `ci_auth` | `oidc` (GitHub, GitLab) or `keypair`. Azure requires `keypair` (OIDC not verified) |
| `oidc_subject_repo` | Exact repo segment of the OIDC `sub` claim. GitHub orgs using immutable IDs need `owner@id/repo@id`; read it from a real token (verify.md) |
| `ci_subject` | GitLab only: custom `sub` claim for merge-request pipelines |
| `oidc_issuer` | Override the default issuer (e.g. self-managed GitLab `https://gitlab.acme.com`, GitHub Enterprise) |
| `cli_version` | Snowflake CLI pinned in pipelines |

## `dbt`

| Field | Meaning |
|---|---|
| `project_dir` | Folder containing `dbt_project.yml`, relative to the repo root |
| `profile_name` | Must equal `profile:` in `dbt_project.yml` |
| `object_name` | dbt project object name. **If discovery found one, reuse it**: deploying under a new name creates a second object |
| `object_schema` | Schema hosting the object and the shared builds in every environment |
| `runtime` | Recorded from discovery. The accelerator never changes the dbt version |
| `threads` | Profile threads (default 8) |
| `extra_env_vars` | `DBT_*` vars found by the `env_var()` scan: `{DBT_X: {default: v, dev: v2, ci: v3}}`. `DBT_ENV_SECRET_*` is rejected (use a Snowflake secret) |

## `warehouse`, `admin_roles`

`warehouse: {name, adopt, size}`. `admin_roles.objects` creates databases, schemas and warehouses.
`admin_roles.security` creates roles and users and issues grants, and **needs MANAGE GRANTS**
(SECURITYADMIN has it; USERADMIN does not).

## `environments` (ordered lowest to highest)

| Field | Meaning |
|---|---|
| `name` | Short lowercase name (`dev`, `qa`, `prod`, `uat`...) |
| `branch` | Git branch that deploys here on merge |
| `target` | Profile target (`--target`) |
| `env_yml_env` | env.yml environment (`--env`). Pairing it with the same environment's `target` is fine; equal to a *different* environment's target is rejected |
| `per_user_schema` | Interactive runs build into `DBT_<user>`; CD always builds into `object_schema` |
| `hosts_ci` | PR builds can land in this database (`CI_PR_<n>` schemas) |
| `database` | `{name, adopt, owner_role}`. `owner_role` = role that owns or will create it |
| `role` | `{name, adopt}`: the build role the CD service user holds |
| `read_role` | Optional `{name, adopt}` read-only role for humans |

`adopt: true` = the object exists; the accelerator only adds missing grants. It is never
recreated, renamed, or has its ownership transferred.

## `ci`

`enabled`, `role`, `schema` (hosts per-PR objects), `env_yml_env` (must not collide with any
target or environment), `pr_schema_prefix`, `default_ci_env` (CI database for feature-branch PRs),
`cleanup`.

PR routing: a PR from an environment branch that `hosts_ci` builds in that branch's database;
any other head builds in `default_ci_env`. Deferral always reads the **base** branch's object.

## `sources`

`{database, schemas: [...]}` -> USAGE + SELECT (current and future) for every build role.
`{database, shared: true, owner_role}` -> `GRANT IMPORTED PRIVILEGES` (shares, Marketplace).

## `packages`

`needs_eai: true` when `packages.yml` exists. Produces batch 50 (account-level, separate approval).

## `schedule`

`{enabled, environment, cron}`: optional scheduled task in addition to CD-on-merge. Created
suspended; resumed after verification.
