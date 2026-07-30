---
name: whats-up
description: >-
  Daily briefing for Snowflake SEs with two distinct sections: (1) My Day —
  today's meetings and outstanding action items; (2) Customer Health — usage
  trends, new AI adoption, and warehouse changes across all accounts.
  Use when: daily standup prep, morning briefing, what's happening today,
  what do I need to do today, whats up, what's up, daily check-in, customer
  usage, customer health, SE daily brief, how are my customers doing.
  Triggers: whats-up, what's up, daily brief, morning check-in, customer health,
  customer usage, my day, today's meetings, what tasks are outstanding.
---

# What's Up — Daily SE Briefing

Two independent sections run in parallel: **My Day** (calendar + tasks) and
**Customer Health** (usage signals across all accounts). Neither depends on the
other — run both simultaneously and present them as distinct parts of the brief.

## Workflow

### Run in Parallel: Part 1 (My Day) + Part 2 (Customer Health)

---

## Part 1: My Day

### Step 1a: Today's External Meetings

Load the `se-meeting-briefing` skill and pull today's external customer-facing
calendar events. For each meeting:
- Time, customer name, attendees
- Quick context: opportunity stage, last interaction summary

### Step 1b: Outstanding Action Items

Use **Glean** as the primary source for action items. Send this prompt to `mcp_glean_chat`:

> "What are my open action items and follow-up tasks from meetings and emails in the
> past 7 days? I'm [SE name] ([email]), a Sales Engineer at Snowflake. Only return
> items assigned to me specifically — not tasks for other people."

Glean synthesizes across Gmail, Gong, Google Calendar, and other indexed sources and
correctly filters to the current user's items. It also excludes already-completed items
and returns source links (email threads, Gong call URLs) for each action item.

Present results as a checklist with source attribution. If Glean is unavailable, fall
back to the Zoom SFDC-scoped query from `meeting-action-items`, filtering `NEXT_STEPS`
to items starting with the SE's full name only.

### Part 1 Output

```
## My Day — [Today's Date]

### Meetings ([count])
1. [Time] — [Customer] — [1-line context]
2. ...

### Action Items ([count])
- [ ] [Task] — [Customer] — [from: meeting title]
- ...
```

Flag meetings happening in <2 hours in **bold**.

---

## Part 2: Customer Health

Run the three queries in `snowhouse_queries.sql` on the **snowhouse** connection,
all in parallel. These run against ALL accounts in `dim_customer_current_user`
(not filtered to today's meetings).

### Step 2a: Revenue trends — MoM + YoY

Query 1 from `snowhouse_queries.sql` — monthly revenue aggregated into 5 categories
(total, compute, AI, CoCo, SPCS) for:
- **Current year**: last 3 calendar months through today (e.g. May–Jul 2026)
- **Prior year**: same 3-month window one year ago (e.g. May–Jul 2025)

Results have one row per (account, month, year). Use them to compute:
- **MoM trend**: how each account's total and AI revenue changed month-to-month
  in the current year
- **YoY comparison**: current month vs same month last year — flag accounts where
  this month's revenue is >20% above or below the prior-year equivalent
- **AI growth signal**: compare `ai_revenue` and `coco_revenue` current vs prior year
  to identify accounts newly adopting or growing AI workloads

Only surface accounts with a notable pattern — flat accounts with no AI signals
and no YoY variance can be omitted from the brief.

### Step 2b: New Cortex Agents

Query 2 from `snowhouse_queries.sql` — agents created in the last 7 days that are
genuinely new (not recreated). Any result here is a high-value signal; call it out
with the account name and full agent name.

### Step 2c: Warehouse changes

Query 3 from `snowhouse_queries.sql` (same logic as `warehouse_usage.sql`) —
new warehouses (first active in last 7 days) and inactive warehouses (active in
days -30 to -8, silent in last 7 days), filtered to >10 avg credits/day.

New warehouses = a workload just started. Inactive warehouses = something shut down.

### Step 2d: Usage anomalies

Query 4 from `snowhouse_queries.sql` — UNPIVOTs all service revenue columns and
compares each day in the last 7 days against its own rolling 7-day prior average.
Results are filtered to spikes ≥$100 above average AND ≥2× average AND ≥$50 absolute.

`spike_amount` = value minus prior average. `spike_ratio` = multiplier vs average.

If results are returned, list them prominently — these are runaway jobs, accidental
loops, or unexpectedly expensive queries the customer may not know about yet.

### Step 2e: Investigate significant spikes

After Step 2d returns results, investigate any spike where `spike_ratio ≥ 5×` OR
`spike_amount ≥ $500`. Route to the appropriate query based on the spike feature type.

**For AI-feature spikes** (feature is `AI_FUNCTIONS`, `AI_SERVICES`, `CORTEX_AGENTS`,
`CORTEX_SEARCH`, or `SNOWFLAKE_INTELLIGENCE`):

Run **Query 6** from `snowhouse_queries.sql` on the **snowhouse** connection.
This queries `METERING2_SAFE_ALL.METERING.AI_SERVICES_CORTEX_FUNCTIONS_METERING_V`
directly — the authoritative AI inference metering table — and returns actual token
and credit costs broken down by user, function, and model.

User identity is extracted from `metadata:role_names` using a regex on `USER$<email>`.
Key output columns:
- `user_email` — who ran the queries (from `USER$` token in role_names)
- `ai_function` — `AI_CLASSIFY`, `AI_COMPLETE`, `AI_EXTRACT`, etc.
- `model` — `llama3.1-8b`, `arctic-extract`, `claude-haiku-4-5`, etc.
- `total_credits` — actual AI inference credits (not compute warehouse credits)
- `total_tokens` — token volume processed

> **Do NOT use `SNOWHOUSE_IMPORT.PROD.JOB_ETL_V`** for user lookups — it's a massive
> union view that will time out.

If Query 6 returns no rows (no AI inference usage in the metering table), run
**Query 6b** (`CORTEX_AGENT_DAY_CREDITS_TOOL_FACT`) as a fallback for Cortex Agent
API call credits.

Key columns to interpret:
- `source` — `CORTEX_FUNCTIONS` (SQL AI function calls) or `CORTEX_AGENTS_SI` (Agent API)
- `function_name` — `COMPLETE`, `AI_EXTRACT`, `AI_CLASSIFY`, `CORTEX_AGENT`, etc.
- `model_name` — which LLM was called (claude, llama, snowflake-arctic, etc.)
- `feature` — `AISQL` (SQL functions), `Cortex Code` (CoCo), `Cortex Agents`, etc.
- `total_tokens` — volume of inference work

**For COMPUTE / SERVERLESS / SNOWPIPE / other non-AI spikes**:

Run **Query 5** from `snowhouse_queries.sql` on the **snowhouse** connection.
Groups `JOB_CREDITS` by warehouse name, tool, client, and tag to identify the workload.

---

**Synthesize into a one-line "Likely cause"** for the anomaly table:
- AI example: "200 `CORTEX_AGENT` calls via `CORTEX_AGENTS_SI`, claude-3-5-sonnet, 1.2M tokens — CI eval runs"
- Compute example: "DBT dag `ams_dag_local` ran 7,811 jobs on `AMS_DATALAKE_ICE_WH_STANDARD` (254 credits)"
- Unknown: "Cause unknown — worth asking the customer"

**Cap at 5 investigations per run** — prioritize by `spike_amount` descending.

### Part 2 Output

```
## Customer Health

### Usage Changes
| Account | Trend | Signal |
|---------|-------|--------|
| ...     | ↑/→/↓ | e.g. "Cortex Agents revenue appeared 2026-07-28" |

### New Cortex Agents
- [Account] — [schema.name] created [date]

### Warehouse Changes
| Account | Warehouse | Status | Avg Credits/Day |
|---------|-----------|--------|-----------------|
| ...     | ...       | New / Inactive | ... |

### ⚠️ Usage Anomalies
| Account | Date | Feature | Spike | Ratio | Likely Cause |
|---------|------|---------|-------|-------|--------------|
| ...     | ...  | ...     | +$X   | N×    | [from Glean or "Unknown — ask customer"] |
```

If no notable changes across any account, output:
`No significant usage changes in the last 7–30 days.`

---

## Stopping Points

- ✋ After both parts — ask if they want to drill into any customer or task

## Notes

- Both parts are independent. If one data source is unavailable, deliver the
  other and note what's missing.
- If the user asks `/whats-up [Customer]`, skip Part 1 and run Part 2 filtered
  to that account only.
- Data sources: Google Calendar + Gong/Zoom (Part 1), snowhouse connection via
  `snowhouse_queries.sql` and `warehouse_usage.sql` (Part 2).

## Output

Write the brief to the **fixed path**:
`~/.snowflake/cortex/skills/whats-up/daily_brief.html`

Always overwrite this file — never use a date-stamped filename. The user has this
path bookmarked and expects it to be refreshed in place each run.

The HTML file uses an inline SVG bar chart (no external libraries) and adapts to
light/dark mode. Use the existing file as the template structure; update the data,
date header, and metadata block with each run.
