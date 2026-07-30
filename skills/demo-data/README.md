# Demo Data Skill

## What It Does

This skill generates realistic synthetic data directly in Snowflake using the "Flaker" pattern (Python UDFs + `GENERATOR`). It produces a complete Medallion Architecture (Bronze / Silver / Gold) with:

- **Bronze**: Raw, intentionally dirty data simulating real ingestion from source systems
- **Silver**: Cleaned, deduplicated, schema-enforced entity tables
- **Gold**: Business-ready star schema with pre-aggregated metrics for dashboards and Cortex Analyst

The generated data includes embedded anomalies and patterns for compelling demo storytelling (declining supplier quality, price spikes, seasonal trends, reshoring patterns, etc.).

## How to Use

The skill as-written is templated for a manufacturing marketplace use case. To customize it for your customer:

1. Open Cortex Code
2. Tell CoCo to adapt the skill for your customer — for example:
   > "Use the demo-data skill but customize it for [customer name]. They are in [industry] and care about [use cases]. Their key entities are [X, Y, Z]."
3. CoCo will rewrite the company context, table schemas, domain-specific UDFs, data ranges, and anomaly stories to match your customer's business.

### Things to customize:

- **Company context** — Replace the industry background with your customer's domain
- **Table schemas** — Adjust entities and relationships to match their data model
- **Domain-specific UDFs** — Generate realistic data for their industry (e.g., financial instruments, patient records, logistics shipments)
- **Pricing/volume ranges** — Match their business scale
- **Anomalies** — Embed patterns relevant to their pain points
- **Naming convention** — Use a generic industry term, never the customer's name

## Output

The skill produces a set of numbered SQL scripts (00-12) plus a semantic model YAML, designed to be executed in order in any Snowflake account.
