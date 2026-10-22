---
name: account_use_case_gap
description: "Snowflake account use case gap analysis. Pulls open Salesforce use cases and ALL new Snowflake objects created in the last 30 days (tables, views, dynamic tables, semantic views, Cortex Agents, Cortex Search services, Streamlit apps, Notebooks, Document AI), clusters them by workload, cross-references against existing use cases, identifies gaps, and generates an HTML gap analysis report with recommended use case names, descriptions, and next steps. Supports an unattended mode (for automations) that runs a whole book of business from a directory of ACCOUNT.md files and writes one roll-up per AE. Use when: use case gap analysis, new objects vs use cases, UC gap, find missing use cases, account review, new AI workloads, what's been built, identify new use cases, account_use_case_gap."
---

# Account Use Case Gap Analysis

## Purpose

Analyze a Snowflake customer account to identify workloads being actively built that lack a matching Salesforce use case. Covers ALL object types: tables, views, dynamic tables, semantic views, Cortex Agents, Cortex Search services, Streamlit apps, Notebooks, and Document AI usage.

Output: a complete HTML gap analysis report with eACV recommendations.

---

## Run Modes

| Mode | Triggered when | Account input | Output |
|---|---|---|---|
| **Interactive** (default) | The request does not supply an accounts directory and does not ask for an unattended run | Step 1 asks for one account name | One report per account (Step 6) |
| **Unattended** | The request supplies `accounts_dir` or an explicit `accounts` list, or says it is unattended / non-interactive / an automation | Step 1U reads `ACCOUNT.md` files | One roll-up per AE (Step 6U) |

Unattended parameters (all supplied by the invoking prompt — never hard-code them in this skill):

- `accounts_dir` (required) — directory whose subfolders each describe one customer in an `ACCOUNT.md`
- `output_dir` (optional) — where the per-AE roll-ups go; default `accounts_dir`. Interactive runs also honour it in place of the Step 6 default path.
- `accounts` (optional) — subset of folder names under `accounts_dir` to run; default all. If given without `accounts_dir`, treat each entry as a path to a folder containing `ACCOUNT.md`; list any entry that can't be resolved under *Skipped / errors*.

**Unattended rules:** never call `ask_user_question`, never stop at a stopping point, and never stop the run because one account failed. If a sub-query fails, apply Query Troubleshooting once, then record the gap ("Streamlit inventory unavailable: <error>") in that account's section and continue; if Step 2U or Step 3 fails, add the account to *Skipped / errors* and move on. If an unattended run has neither `accounts_dir` nor `accounts`, write nothing and report "no accounts_dir supplied" in the summary. All Snowflake access is read-only. Pass these rules on to any subagent that processes an account.

---

## Step 1: Gather Account Name (interactive)

Use `ask_user_question` (type: "text") to collect only the **customer/account name** (company name to search). No other inputs needed — AE name and account selection are resolved automatically.

## Step 1U: Load Accounts (unattended)

List the subfolders of `accounts_dir` that contain an `ACCOUNT.md`; skip any folder without one. From the header block of each `ACCOUNT.md` read:

- **Display name** — the first `# ` heading
- **Salesforce ID(s)** — the 18-character IDs on the `Salesforce ID` / `Salesforce IDs` line that identify this customer (an account may list more than one, e.g. two related companies). Ignore any ID the line marks as excluded, unrelated, wrong, or not this customer — e.g. in `0013100001AAAAAAAA (unrelated account named "Acme" — 001AB00000BBBBBBBB — is excluded)`, use only the first ID.
- **AE** — the `AE:` line with any trailing parenthetical note removed (`Jane Doe (both)` → `Jane Doe`); this is the grouping key for Step 6U. If there is no AE line, group the account under `Unassigned`.
- **Deployments** — the `Deployments` line, used only as a cross-check in Step 2U

If a file has no Salesforce ID, add the account to *Skipped / errors* ("no Salesforce ID in ACCOUNT.md") and move on. Do not fall back to name search in unattended mode — name matches pull in look-alike Salesforce records that an `ACCOUNT.md` may deliberately exclude.

Then run Steps 2U–5 for each account. For large books, process accounts in parallel subagents when available; each returns a compact per-account record (resolved account, AI inventory counts, clusters, gap cards, covered UCs, next steps) so the parent only holds records, not raw query output. Keep any intermediate files in a temp directory, not in `output_dir`.

---

## Step 2: Auto-Resolve Account & AE

Run both queries in parallel immediately — no user prompts.

**2a: Find the highest-revenue Snowflake account** — auto-select the first result (most activity):

```sql
SELECT
    a.salesforce_account_id,
    a.salesforce_account_name,
    a.snowflake_account_id,
    a.snowflake_deployment,
    a.snowflake_account_name AS locator,
    ROUND(SUM(m.product_revenue_usd_total), 0) AS revenue_30d_usd
FROM SNOWSCIENCE.DIMENSIONS.DIM_SNOWFLAKE_ACCOUNTS a
LEFT JOIN SNOW_CERTIFIED.SNOWFLAKE_ACCOUNT_DEPLOYMENT.AGG_MONTHLY_SNOWFLAKE_ACCOUNT_REVENUE m
    ON a.snowflake_account_id = CAST(m.snowflake_account_id AS INTEGER)
    AND m.month_at >= DATEADD(month, -1, DATE_TRUNC('month', CURRENT_DATE()))
WHERE a.salesforce_account_name ILIKE '%{account_name}%'
GROUP BY 1, 2, 3, 4, 5
ORDER BY revenue_30d_usd DESC NULLS LAST
LIMIT 1
```

If no results, fall back to name search and take the first match:

```sql
SELECT TOP 1 salesforce_account_id, salesforce_account_name
FROM SALES.RAVEN.D_SALESFORCE_ACCOUNT_CUSTOMERS
WHERE salesforce_account_name ILIKE '%{account_name}%'
ORDER BY salesforce_account_name
```

Store as `{salesforce_id}`, `{account_id}`, `{deployment}` (lowercase, used as-is in SNOWSCIENCE queries).

**2b: Look up AE and SE names** from SALES.RAVEN (run in parallel with 2a):

```sql
SELECT
    salesforce_owner_name AS ae_name,
    lead_sales_engineer_name AS se_name
FROM SALES.RAVEN.D_SALESFORCE_ACCOUNT_CUSTOMERS
WHERE salesforce_account_id = '{salesforce_id}'
LIMIT 1
```

Use `ae_name` and `se_name` in the report header. If not found, omit.

Proceed immediately to Step 3 — no user prompt needed.

## Step 2U: Resolve by Salesforce ID (unattended)

Replace the name searches in 2a with the IDs from `ACCOUNT.md`, and take the highest-revenue Snowflake account **per Salesforce ID**:

```sql
SELECT
    a.salesforce_account_id,
    a.salesforce_account_name,
    a.snowflake_account_id,
    a.snowflake_deployment,
    a.snowflake_account_name AS locator,
    ROUND(SUM(m.product_revenue_usd_total), 0) AS revenue_30d_usd
FROM SNOWSCIENCE.DIMENSIONS.DIM_SNOWFLAKE_ACCOUNTS a
LEFT JOIN SNOW_CERTIFIED.SNOWFLAKE_ACCOUNT_DEPLOYMENT.AGG_MONTHLY_SNOWFLAKE_ACCOUNT_REVENUE m
    ON a.snowflake_account_id = CAST(m.snowflake_account_id AS INTEGER)
    AND m.month_at >= DATEADD(month, -1, DATE_TRUNC('month', CURRENT_DATE()))
WHERE a.salesforce_account_id IN ({salesforce_ids})   -- quoted, comma-separated IDs from Step 1U
GROUP BY 1, 2, 3, 4, 5
QUALIFY ROW_NUMBER() OVER (PARTITION BY a.salesforce_account_id ORDER BY revenue_30d_usd DESC NULLS LAST) = 1
```

Run Step 4 once per returned Snowflake account; run Step 3 with `salesforce_account_id IN ({salesforce_ids})`. If an ID returns neither a Snowflake account nor any use cases, list it under *Skipped / errors* ("Salesforce ID not found"). If it has use cases but no Snowflake account, keep it and note "no Snowflake account resolved". If the resolved account differs from the primary named on the `ACCOUNT.md` Deployments line, use the resolved one and note the difference.

Run 2b for each ID. Group the roll-up by the `ACCOUNT.md` AE; if Salesforce's `ae_name` differs, show both in the account section. Take the header SE from 2b's `se_name` (most common value across the AE's accounts).

---

## Step 3: Pull Open Use Cases

Run in parallel with Step 4.

```sql
SELECT
    use_case_name, use_case_stage, use_case_status,
    use_case_description, use_case_comments, next_steps,
    workloads, use_case_acv, last_modified_date,
    is_in_pursuit, is_in_implementation, is_in_production, is_lost, technical_win
FROM SALES.RAVEN.SDA_USE_CASE
WHERE salesforce_account_id = '{salesforce_id}'
ORDER BY last_modified_date DESC
```

---

## Step 4: Pull New Snowflake Objects (last 30 days)

Use **`SNOWSCIENCE.LIVE_OBJECTS.ALL_LIVE_*`** tables — cross-deployment, no schema conversion needed. Run all sub-queries in parallel.

**Exception — 4c, 4e, 4f use `SNOWHOUSE_IMPORT.{DEPLOYMENT_UPPER}.*_ETL_V`.** `ALL_LIVE_SEMANTIC_VIEWS`, `ALL_LIVE_CORTEX_SEARCH_SERVICES`, and `ALL_LIVE_STREAMLITS` are not readable by the `SALES_ENGINEER` role (`does not exist or not authorized`). The Snowhouse views are per-deployment: substitute `{DEPLOYMENT_UPPER}` (e.g. `va2` → `VA2`) and omit `deployment` predicates. They return one row per live instance, so these queries `GROUP BY name` to count distinct objects.

### Counting AI Objects: Live vs New vs Updated (4c–4g)

Most teams maintain semantic views, agents, search services, Streamlit apps and notebooks with `CREATE OR REPLACE`. Every replace leaves a deleted older row behind, so an anti-join that drops any name with an older deleted version **hides long-lived objects that are still in use** — on one account it reduced 26 live semantic views to 2 and removed its only agent, which erased the main gap finding.

Instead, group every version of an object by name and derive status from its history:

```sql
-- Pattern for 4c–4g: one row per object name across all versions (live and deleted)
GROUP BY obj.name
HAVING COUNT_IF(obj.deleted_on IS NULL) > 0                          -- live: at least one undeleted version
-- MIN(obj.created_on) >= DATEADD(day, -30, CURRENT_DATE())  AS is_new_30d   -- first-ever version is recent = genuinely new
-- MAX(obj.created_on) >= DATEADD(day, -30, CURRENT_DATE())  AS updated_30d  -- replaced/rebuilt recently = active development
```

Report `is_new_30d` objects as new work, `updated_30d` (but not new) objects as active development, and the full live list as the AI inventory. Nightly drop-and-recreate jobs stay out of "new" because their first version is old.

### Drop-and-Recreate Filter Pattern (tables only — 4a, 4b)

For tables, daily CDC pipelines drop and recreate the same table, which makes it look new every day. 4a/4b keep this anti-join to exclude those rebuilds:

```sql
-- Join this LEFT OUTER JOIN to the same table (suffixed _old) to exclude drop-and-recreate:
LEFT JOIN SNOWSCIENCE.LIVE_OBJECTS.ALL_LIVE_<TYPE> obj_old
  ON obj.account_id = obj_old.account_id
  AND obj.deployment = obj_old.deployment
  AND obj.name = obj_old.name
  AND obj_old.deleted_on IS NOT NULL           -- a deleted version exists
  AND obj_old.created_on < DATEADD(day, -30, CURRENT_DATE())  -- deleted version is older
-- Then add in WHERE: AND obj_old.name IS NULL  (no prior deleted version = truly new)
```

### Common Join to Get Schema/Database Context

For objects that store `parent_id` pointing to a schema:

```sql
JOIN SNOWSCIENCE.LIVE_OBJECTS.ALL_LIVE_SCHEMAS s
  ON obj.account_id = s.account_id
  AND obj.deployment = s.deployment
  AND obj.parent_id = s.id
  AND s.deleted_on IS NULL
  AND s.ds = CURRENT_DATE()
```

---

**Note on daily-recreation inflation**: Some ETL pipelines drop and recreate the same table daily (CDC snapshots). This inflates counts since each creation event is a separate row. Use `COUNT(DISTINCT t.name)` for unique-object counts rather than `COUNT(*)` to avoid overcounting. You can spot inflation when the same table name appears many times with consecutive dates — this is a pipeline, not a new workload.

### 4a: Object Cluster Summary — Tables, Views, Dynamic Tables

Use `COUNT(DISTINCT t.name)` to avoid inflation from daily-recreated CDC tables:

```sql
SELECT
    db.name AS database_name,
    s.name  AS schema_name,
    COUNT(DISTINCT t.name) AS unique_objects,
    MIN(t.created_on)::DATE AS earliest,
    MAX(t.created_on)::DATE AS latest
FROM SNOWSCIENCE.LIVE_OBJECTS.ALL_LIVE_TABLES t
JOIN SNOWSCIENCE.LIVE_OBJECTS.ALL_LIVE_SCHEMAS s
    ON t.account_id = s.account_id AND t.deployment = s.deployment
    AND t.parent_id = s.id AND s.deleted_on IS NULL AND s.ds = CURRENT_DATE()
JOIN SNOWSCIENCE.LIVE_OBJECTS.ALL_LIVE_DATABASES db
    ON s.account_id = db.account_id AND s.deployment = db.deployment
    AND s.parent_id = db.id AND db.deleted_on IS NULL AND db.ds = CURRENT_DATE()
LEFT JOIN SNOWSCIENCE.LIVE_OBJECTS.ALL_LIVE_TABLES t_old
    ON t.account_id = t_old.account_id AND t.deployment = t_old.deployment
    AND t.name = t_old.name AND t.parent_id = t_old.parent_id
    AND t_old.deleted_on IS NOT NULL
    AND t_old.created_on < DATEADD(day, -30, CURRENT_DATE())
WHERE t.account_id = {account_id}
  AND t.deployment = '{deployment}'
  AND t.created_on >= DATEADD(day, -30, CURRENT_TIMESTAMP())
  AND t.deleted_on IS NULL
  AND t_old.name IS NULL
  AND db.name != 'SNOWFLAKE'
GROUP BY db.name, s.name
ORDER BY unique_objects DESC
LIMIT 60
```

### 4b: Table/View Detail (top clusters — for workload inference)

`ALL_LIVE_TABLES` has no `kind` or `comment` column; use `DEFINITION` for view inference. Use DISTINCT to get one row per unique table name:

```sql
SELECT DISTINCT
    db.name AS database_name,
    s.name  AS schema_name,
    t.name  AS object_name,
    MAX(t.created_on)::DATE AS created_date,
    LEFT(MAX(t.definition), 400) AS definition_preview
FROM SNOWSCIENCE.LIVE_OBJECTS.ALL_LIVE_TABLES t
JOIN SNOWSCIENCE.LIVE_OBJECTS.ALL_LIVE_SCHEMAS s
    ON t.account_id = s.account_id AND t.deployment = s.deployment
    AND t.parent_id = s.id AND s.deleted_on IS NULL AND s.ds = CURRENT_DATE()
JOIN SNOWSCIENCE.LIVE_OBJECTS.ALL_LIVE_DATABASES db
    ON s.account_id = db.account_id AND s.deployment = db.deployment
    AND s.parent_id = db.id AND db.deleted_on IS NULL AND db.ds = CURRENT_DATE()
LEFT JOIN SNOWSCIENCE.LIVE_OBJECTS.ALL_LIVE_TABLES t_old
    ON t.account_id = t_old.account_id AND t.deployment = t_old.deployment
    AND t.name = t_old.name AND t.parent_id = t_old.parent_id
    AND t_old.deleted_on IS NOT NULL
    AND t_old.created_on < DATEADD(day, -30, CURRENT_DATE())
WHERE t.account_id = {account_id}
  AND t.deployment = '{deployment}'
  AND t.created_on >= DATEADD(day, -30, CURRENT_TIMESTAMP())
  AND t.deleted_on IS NULL
  AND t_old.name IS NULL
  AND db.name != 'SNOWFLAKE'
GROUP BY db.name, s.name, t.name
ORDER BY created_date DESC
LIMIT 300
```

### 4c: Semantic Views (all — AI footprint signal, not just 30-day)

```sql
SELECT
    sv.name AS semantic_view_name,
    MIN(sv.created_on)::DATE AS first_created,
    MAX(sv.created_on)::DATE AS last_updated,
    MIN(sv.created_on) >= DATEADD(day, -30, CURRENT_DATE()) AS is_new_30d,
    MAX(sv.created_on) >= DATEADD(day, -30, CURRENT_DATE()) AS updated_30d,
    MAX(IFF(sv.deleted_on IS NULL, sv.comment, NULL)) AS comment
FROM SNOWHOUSE_IMPORT.{DEPLOYMENT_UPPER}.SEMANTIC_VIEW_ETL_V sv
WHERE sv.account_id = {account_id}
GROUP BY sv.name
HAVING COUNT_IF(sv.deleted_on IS NULL) > 0
ORDER BY last_updated DESC
LIMIT 100
```

### 4d: Cortex Agents (all — most agents live longer than 30 days)

`ALL_LIVE_AGENTS` has no `comment` column — use the SNOWHOUSE fallback for descriptions:

```sql
SELECT
    ca.name,
    MIN(ca.created_on)::DATE AS first_created,
    MAX(ca.created_on)::DATE AS last_updated,
    MIN(ca.created_on) >= DATEADD(day, -30, CURRENT_DATE()) AS is_new_30d,
    MAX(ca.created_on) >= DATEADD(day, -30, CURRENT_DATE()) AS updated_30d,
    MAX(IFF(ca.deleted_on IS NULL, ca.comment, NULL)) AS description
FROM SNOWHOUSE_IMPORT.{DEPLOYMENT_UPPER}.CORTEX_AGENT_ETL_V ca
WHERE ca.account_id = {account_id}
GROUP BY ca.name
HAVING COUNT_IF(ca.deleted_on IS NULL) > 0
ORDER BY last_updated DESC
LIMIT 100
```

Alternatively, use SNOWSCIENCE for existence check only (no description):

```sql
SELECT
    ag.name,
    MIN(ag.created_on)::DATE AS first_created,
    MAX(ag.created_on)::DATE AS last_updated,
    MIN(ag.created_on) >= DATEADD(day, -30, CURRENT_DATE()) AS is_new_30d
FROM SNOWSCIENCE.LIVE_OBJECTS.ALL_LIVE_AGENTS ag
WHERE ag.account_id = {account_id}
  AND ag.deployment = '{deployment}'
GROUP BY ag.name
HAVING COUNT_IF(ag.deleted_on IS NULL) > 0
ORDER BY last_updated DESC
LIMIT 100
```

### 4e: Cortex Search Services (all)

```sql
SELECT
    cs.name AS service_name,
    MIN(cs.created_on)::DATE AS first_created,
    MAX(cs.created_on)::DATE AS last_updated,
    MIN(cs.created_on) >= DATEADD(day, -30, CURRENT_DATE()) AS is_new_30d,
    MAX(cs.created_on) >= DATEADD(day, -30, CURRENT_DATE()) AS updated_30d,
    COUNT(DISTINCT IFF(cs.deleted_on IS NULL, cs.id, NULL)) AS live_instances,
    MAX(IFF(cs.deleted_on IS NULL, cs.comment, NULL)) AS comment
FROM SNOWHOUSE_IMPORT.{DEPLOYMENT_UPPER}.CORTEX_SEARCH_SERVICE_ETL_V cs
WHERE cs.account_id = {account_id}
GROUP BY cs.name
HAVING COUNT_IF(cs.deleted_on IS NULL) > 0
ORDER BY last_updated DESC
LIMIT 100
```

The same service name often exists in several schemas — report distinct names, and use `live_instances` only as a scale signal.

Cross-deployment alternative: `SNOWSCIENCE.LLM.CORTEX_SEARCH_SERVICES` (`account_id`, `deployment`, `name`, `database`, `schema`, `created_on`) is readable by `SALES_ENGINEER`, but has no `deleted_on` and appears to include dropped services. `SNOWSCIENCE.LLM.CORTEX_SEARCH_DAILY_ACTIVE_SERVICES` adds daily request counts split by agent / Analyst / direct use — useful as a usage signal.

### 4f: Streamlit Apps (all)

```sql
SELECT
    sl.name AS app_name,
    MIN(sl.created_on)::DATE AS first_created,
    MAX(sl.created_on)::DATE AS last_updated,
    MIN(sl.created_on) >= DATEADD(day, -30, CURRENT_DATE()) AS is_new_30d,
    MAX(sl.created_on) >= DATEADD(day, -30, CURRENT_DATE()) AS updated_30d,
    MAX(IFF(sl.deleted_on IS NULL, sl.comment, NULL)) AS comment,
    MAX(IFF(sl.deleted_on IS NULL, sl.title, NULL)) AS title
FROM SNOWHOUSE_IMPORT.{DEPLOYMENT_UPPER}.STREAMLIT_ETL_V sl
WHERE sl.account_id = {account_id}
GROUP BY sl.name
HAVING COUNT_IF(sl.deleted_on IS NULL) > 0
ORDER BY last_updated DESC
LIMIT 150
```

Prioritize named apps (not auto-generated hash names) and apps with a meaningful `comment` or `title`. Workspace-published apps have hash names (`ST98A4…`) but a readable `title`; their `comment` is JSON containing the source workspace path and publisher user ID.

### 4g: Notebooks (last 30 days)

```sql
SELECT
    nb.name AS notebook_name,
    MIN(nb.created_on)::DATE AS first_created,
    MAX(nb.created_on)::DATE AS last_updated,
    MIN(nb.created_on) >= DATEADD(day, -30, CURRENT_DATE()) AS is_new_30d,
    MAX(nb.sf_sit_comment) AS comment
FROM SNOWSCIENCE.LIVE_OBJECTS.ALL_LIVE_NOTEBOOKS nb
WHERE nb.account_id = {account_id}
  AND nb.deployment = '{deployment}'
GROUP BY nb.name
HAVING COUNT_IF(nb.deleted_on IS NULL) > 0
   AND MAX(nb.created_on) >= DATEADD(day, -30, CURRENT_DATE())
ORDER BY last_updated DESC
LIMIT 50
```

`ALL_LIVE_NOTEBOOKS` has no `comment` column — the comment is in `SF_SIT_COMMENT`.

### 4h: Document AI Usage (last 30 days)

`ALL_LIVE_DOCUMENT_UNDERSTANDING` is not readable by `SALES_ENGINEER`, and classic Document AI project tracking (`SNOWSCIENCE.SNOWML.DOCUMENT_AI_ACTIVE_PROJECTS`) stopped refreshing in April 2026. Measure document AI by **usage** instead: `AI_PARSE_DOCUMENT` from `LLM.CORTEX_LLM_PARSE_DOC_CREDITS`, plus `AI_EXTRACT` and any AI function called on a document (`doc = TRUE`) from `LLM.CORTEX_LLM_TOKEN_CREDITS`. Both are cross-deployment.

```sql
WITH usage AS (
    SELECT ds, model_name AS function_model, credits
    FROM SNOWSCIENCE.LLM.CORTEX_LLM_PARSE_DOC_CREDITS
    WHERE account_id = {account_id}
      AND LOWER(deployment) = '{deployment}'
      AND ds >= DATEADD(day, -30, CURRENT_DATE())
    UNION ALL
    SELECT ds, function_model, credits
    FROM SNOWSCIENCE.LLM.CORTEX_LLM_TOKEN_CREDITS
    WHERE account_id = {account_id}
      AND LOWER(deployment) = '{deployment}'
      AND ds >= DATEADD(day, -30, CURRENT_DATE())
      AND (function_model ILIKE 'AI_EXTRACT%' OR doc = TRUE)
)
SELECT
    function_model,
    COUNT(DISTINCT ds) AS active_days,
    ROUND(SUM(credits), 2) AS credits,
    MIN(ds) AS first_ds,
    MAX(ds) AS latest_ds
FROM usage
GROUP BY function_model
ORDER BY credits DESC
```

This returns usage, not named objects — treat any `AI_EXTRACT` / `AI_PARSE_DOCUMENT` activity as a document-processing signal and look in 4a/4b for the stages/tables it reads from to name the workload.

---

## Query Troubleshooting

If a `SNOWSCIENCE.LIVE_OBJECTS.ALL_LIVE_<TYPE>` table does not exist or returns an error, fall back to the `SNOWHOUSE_IMPORT.{DEPLOYMENT_UPPER}.<TYPE>_ETL_V` equivalent (uppercase deployment, e.g. `va2` → `VA2`). The Snowscience names map roughly as:

| SNOWSCIENCE | SNOWHOUSE fallback |
|---|---|
| `ALL_LIVE_TABLES` | `TABLE_ETL_V` (+ SCHEMA_ETL_V + DATABASE_ETL_V joins) |
| `ALL_LIVE_SEMANTIC_VIEWS` | `SEMANTIC_VIEW_ETL_V` |
| `ALL_LIVE_AGENTS` | `CORTEX_AGENT_ETL_V` |
| `ALL_LIVE_CORTEX_SEARCH_SERVICES` | `CORTEX_SEARCH_SERVICE_ETL_V` |
| `ALL_LIVE_STREAMLITS` | `STREAMLIT_ETL_V` |
| `ALL_LIVE_NOTEBOOKS` | `NOTEBOOK_ETL_V` |
| `ALL_LIVE_DOCUMENT_UNDERSTANDING` | none — use the usage-based 4h query (`LLM.CORTEX_LLM_PARSE_DOC_CREDITS` + `LLM.CORTEX_LLM_TOKEN_CREDITS`) |

Queries 4c–4g above already use the live/new/updated pattern. For other types, the Snowhouse fallback does not include any recreate handling unless you add it — results may contain more noise.

---

## Step 5: Analyze and Cluster

Group all collected objects into logical workload clusters. Signals to use:

| Signal | Inference |
|---|---|
| Database prefix (`ENT_GIS_*`, `ENT_GC_*`) | Business unit |
| Schema layer (`LANDING`, `STAGING`, `INTEGRATION`, `PRESENTATION`) | ETL pipeline depth |
| Schema names (`DATA_SCIENCE`, `SIGMA_WRITEBACK`, `AI_RESEARCH`) | Workload type |
| Table name patterns (`FACT_`, `DIM_`, `STG_`) | Dimensional modeling |
| Table subject area (`COMMITMENT`, `FUND`, `VALUATION`) | Domain |
| Agent/search names (`IRR_AGENT`, `DDQ_*`, `LPA_*`) | AI use case |
| Streamlit names (`LPA_INTAKE_APP`, `DEBT_DASHBOARD`) | Application product |
| DEV only vs DEV+QA vs PROD | Stage (2–3, 3–4, 4–7) |

For each cluster: cluster name, evidence list, workload type, likely owner, stage, UC match.

---

## Step 6: Generate HTML Report

Produce a single self-contained HTML file following Snowflake brand guidelines.

**File path** (interactive): `~/Documents/coco/customers/{account_name}/use_case_gap_analysis_{YYYYMMDD}.html`, or `{output_dir}/use_case_gap_analysis_{account_slug}_{YYYYMMDD}.html` if `output_dir` was given (`account_slug` = account name, slugged the same way as `ae_slug` in Step 6U). In unattended mode, use Step 6U instead.

### Report Sections

```
1. Sticky header: account name, AE, deployment, date, total gap eACV
2. Quick stats: new object counts by type, gaps found, active UC pipeline
3. AI Inventory: Agents | Search services | Semantic Views | Streamlit apps (collapsible)
4. Object Clusters: table by database/schema, count, date range, inferred workload
5. Gap Analysis: one card per gap
6. Covered Use Cases: active UC table
7. Next Steps: prioritized actions
```

### Gap Card Fields

- Priority badge: VERY HIGH (red) / HIGH (orange) / MEDIUM (yellow)
- Suggested UC name (Salesforce-ready)
- Signal evidence: bulleted object names
- Description: 2–3 sentences for Salesforce UC Description field
- Workload | Likely Owner | Suggested Stage | eACV range
- Prioritized Feature (Cortex Agents / Document AI / Cortex Search / Cortex Analyst)

### Styling

```
--sf-blue: #29b5e8  |  --sf-dark: #1a2332  |  VERY HIGH: #dc2626  |  HIGH: #ea580c  |  MEDIUM: #ca8a04
```

## Step 6U: Generate Per-AE Roll-ups (unattended)

Write one self-contained HTML file per AE (same styling and gap card fields as Step 6):

**File path**: `{output_dir}/use_case_gap_{ae_slug}_{YYYYMMDD}.html` — `ae_slug` is the AE name lowercased, with each run of non-alphanumeric characters replaced by `_` and leading/trailing `_` trimmed (e.g. `jane_doe`). A rerun on the same day overwrites that day's file; never modify files with other names or dates.

Totals: report gap eACV as a range; sum the low ends and the high ends separately, and sort account sections by the high end.

```
1. Header: AE, SE, date, accounts covered, total gaps, total gap eACV
2. Account summary table: account | resolved Snowflake account(s) | gaps | gap eACV | new AI objects (30d, `is_new_30d` count across 4c–4g) | active UCs
3. One collapsible section per account (largest gap eACV high end first) containing Step 6 sections 2–7
4. Skipped / errors: account | reason
```

---

## Step 7: Present and Save

Save the HTML file. Present chat summary: object counts by type, gap count, eACV range, file path.

In unattended mode, end with a summary readable in an automation run log: each file written, accounts processed vs skipped (with reasons), and gap count + gap eACV per AE.

---

## Stopping Points (interactive only)

- ✋ After Step 1 — wait for account name only
- ✋ If Step 2 returns no matching accounts — inform user and ask for alternate spelling

Unattended mode has no stopping points (see Run Modes).

---

## Use Case Status Reference

- **Include**: `In Pursuit`, `Implementation`, `Production`
- **Skip**: `Not In Pursuit`, `Use Case Lost`
