**Subject: Generate SQL Scripts for Synthetic Snowflake Data Using Python UDFs (Flaker Pattern) with Medallion Architecture (Bronze → Silver → Gold)**

You are a principal sales engineer with decades of experience in the manufacturing and industrial technology industry. Your specialty is building customer-relevant demos, which includes realistic synthetic data. Your task is to create a set of SQL scripts that generate realistic synthetic data **directly in Snowflake** for a demo for **Xometry** (NASDAQ: XMTR), the world's largest AI-powered on-demand manufacturing marketplace.

**IMPORTANT — Demo Asset Naming Convention**: Do **not** use the customer's name (Xometry) in any demo assets, including database names, schema names, table names, UDF names, YAML files, README content, variable names, or SQL comments. Instead, use a **generic industry term** such as `MANUFACTURING_MARKETPLACE`, `MFG_DEMO`, or simply `MANUFACTURING` as the identifier. This ensures the demo assets are reusable across prospects and do not expose the customer relationship. For example, use `MANUFACTURING_DEMO.BRONZE` instead of `MANUFACTURING_DEMO.BRONZE`.

Instead of generating data locally with Python and loading CSV files, you will use **Snowflake Python UDFs** (the "Flaker" pattern) to generate fake data natively in SQL. This approach uses the `faker` library available in Snowflake's Anaconda channel via Python UDFs, combined with `table(generator(rowcount => N))` to produce rows at scale.

**The data must be organized into a three-layer Medallion Architecture:**

| Layer | Schema | Purpose | Data Quality |
|---|---|---|---|
| **Bronze** | `MANUFACTURING_DEMO.BRONZE` | Raw synthetic data as-generated. Append-only, no cleanup. Includes ingestion metadata. | Raw, unvalidated |
| **Silver** | `MANUFACTURING_DEMO.SILVER` | Cleaned, deduplicated, schema-enforced, conformed entity tables. Referential integrity validated. | Validated, typed, consistent |
| **Gold** | `MANUFACTURING_DEMO.GOLD` | Business-ready dimensional model (star schema), pre-aggregated analytics, and metrics tables optimized for BI/dashboards/Cortex Analyst. | Business-approved, documented |

### **The Flaker Pattern — Python UDFs for Fake Data in Snowflake**

The core technique is to create a Python UDF that wraps the `faker` library, then call it from SQL:

```sql
CREATE OR REPLACE FUNCTION FAKE(locale VARCHAR, provider VARCHAR, params VARIANT)
RETURNS VARIANT
LANGUAGE PYTHON
VOLATILE
RUNTIME_VERSION = '3.11'
PACKAGES = ('faker', 'simplejson')
HANDLER = 'fake'
AS $$
import faker
import simplejson as json

def fake(locale, provider, params):
    if not isinstance(params, dict):
        params = {}
    f = faker.Faker(locale)
    return json.loads(json.dumps(getattr(f, provider)(**params), default=str))
$$;
```

> **Important**: The `params` guard must use `if not isinstance(params, dict)` — not `if params is None` — because Snowflake passes a `sqlNullWrapper` object for SQL NULL, which is not Python `None`.
>
> **Important**: The `PACKAGES` clause must not have a trailing comma — use `('simplejson')` not `('simplejson',)`.

Usage examples:
```sql
SELECT FAKE('en_US','name',NULL)::VARCHAR AS fake_name
FROM TABLE(GENERATOR(ROWCOUNT => 50));

SELECT FAKE('en_US','date_between',{'start_date':'-1095d','end_date':'today'})::DATE AS fake_date
FROM TABLE(GENERATOR(ROWCOUNT => 1000));
```

You should also create **additional custom Python UDFs** beyond the generic FAKE function for domain-specific data that the Faker library doesn't cover (e.g., realistic part names, manufacturing process parameters, CAD geometry metadata, supplier capability profiles, quote cost breakdowns, quality inspection measurements). These UDFs should use `random` for numerical distributions and return VARIANT for complex nested structures.

### **Xometry — Company Context**

Xometry is an AI-powered online marketplace that connects buyers needing custom manufactured parts with a global network of over 4,200 suppliers. Founded in 2013, headquartered in North Bethesda, Maryland, Xometry serves 78,000+ buyers including ~30% of Fortune 500 companies such as BMW, NASA, Bosch, Dell, and General Electric.

**Key Business Areas:**

* **Instant Quoting Engine**: AI/ML-powered pricing that analyzes uploaded CAD files to generate instant, accurate quotes based on geometry, volume, material, manufacturing process, and location.
* **Manufacturing Processes**: CNC machining (milling, turning, 5-axis), 3D printing (SLS, SLA, FDM, DMLS, MJF), injection molding, urethane casting, die casting, sheet metal fabrication, and compression molding.
* **Supplier Matching**: Intelligent routing of orders to optimal suppliers based on process capabilities, capacity, quality ratings, geography, and lead time requirements.
* **Workcenter & Teamspace**: Cloud-based tools for supplier job management and buyer collaboration on orders.
* **Thomas Network Integration**: B2B supplier discovery platform with 500,000+ commercial and industrial suppliers.
* **Industries Served**: Aerospace & defense, automotive, healthcare/medical devices, consumer goods, industrial equipment, robotics, and energy.

**Geographic Distribution:**

* **Buyers**: Global, concentrated in US tech hubs (San Francisco, Boston, Austin, Seattle, Detroit), Europe (Munich, Stuttgart, London), and Asia (Shanghai, Tokyo, Shenzhen).
* **Suppliers**: Manufacturing hubs in the US Midwest (Ohio, Michigan, Indiana, Wisconsin), Southeast US (North Carolina, Tennessee, Georgia), Southern China (Guangdong, Zhejiang), Germany (Baden-Württemberg, Bavaria), and Japan (Aichi, Osaka).

-----

## **1. Core Requirements**

* Your main goal is to generate a set of SQL scripts that are robust, realistic, and easy to execute in any Snowflake account.
* **All data generation happens inside Snowflake** — no local Python scripts, no CSV/JSON file loading. Use Python UDFs, `GENERATOR()`, `RANDOM()`, `UNIFORM()`, `SEQ4()`, `OBJECT_CONSTRUCT()`, `ARRAY_CONSTRUCT()`, and other Snowflake-native functions.
* **Data must flow through the Medallion Architecture**: raw generation lands in **Bronze**, cleaning/conforming produces **Silver**, and business modeling/aggregation creates **Gold**. Each layer lives in its own Snowflake schema.
* The fact data should contain anomalies for things like seasonality (e.g., Q4 order surges, summer production slowdowns), supply chain disruptions (e.g., material shortages, shipping delays), and significant industry events (e.g., tariff changes, semiconductor shortages, reshoring trends from Asia to US).
* The scripts should include DDL to create all tables, the Python UDFs, and INSERT/CTAS statements to populate them.
* A Snowflake semantic view or model file that conforms to the Snowflake standard, built on the **Gold** layer.
* VARIANT columns should contain nested JSON structures (e.g., part geometry metadata with features/tolerances, supplier capability profiles with machine inventories, quote cost breakdowns with material/labor/overhead components). Use OBJECT_CONSTRUCT / ARRAY_CONSTRUCT in SQL or return complex dicts/lists from Python UDFs.
* Include example SELECT statements that demonstrate querying VARIANT data (lateral flatten, dot notation, bracket notation) as well as cross-layer lineage queries.
* Logging/progress should be visible via SQL comments and can use `SYSTEM$LOG()` in stored procedures if wrapped in a master procedure.

-----

## **2. Medallion Architecture — Layer Definitions**

### **2a. Bronze Layer (`MANUFACTURING_DEMO.BRONZE`)**

Bronze is the raw data landing zone. Tables here represent data "as generated" with no cleanup or validation. This simulates raw ingestion from source systems (ERP, quoting engine, IoT, CRM).

**Bronze Design Principles:**
* **Append-only** — never UPDATE or DELETE in Bronze.
* **Include ingestion metadata** on every table:
  * `_ingested_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()` — when the record landed.
  * `_source_system VARCHAR` — simulated source (e.g., `'quoting_engine'`, `'erp'`, `'crm'`, `'iot_sensors'`, `'supplier_portal'`).
  * `_batch_id VARCHAR` — simulated batch identifier (e.g., `'batch-' || TO_CHAR(CURRENT_DATE(), 'YYYYMMDD') || '-' || SEQ4()`).
* **Minimal type enforcement** — store loosely typed data. Where practical, use VARCHAR for fields that would normally be INT or DATE (to simulate raw ingestion imperfections). VARIANT columns store raw JSON payloads as-is.
* **Intentionally include dirty data** (~2-5% of rows):
  * NULL values in fields that should not be NULL.
  * Duplicate records (same business key, different `_ingested_at`).
  * Inconsistent date formats (mix of `'YYYY-MM-DD'`, `'MM/DD/YYYY'`, `'DD-Mon-YYYY'`).
  * Inconsistent casing (`'CNC Milling'` vs `'cnc milling'` vs `'CNC MILLING'`).
  * Out-of-range values (negative prices, future dates for historical records, quality scores > 100).
  * Orphan foreign keys (references to non-existent IDs).

**Bronze Tables:**

| Table | Simulated Source | Description |
|---|---|---|
| `raw_buyers` | `crm` | Raw buyer/company records with inconsistent formatting |
| `raw_suppliers` | `supplier_portal` | Raw supplier registrations with dirty data |
| `raw_materials` | `erp` | Material catalog exports with some missing properties |
| `raw_manufacturing_processes` | `erp` | Process definitions |
| `raw_machines` | `iot_sensors` | Machine registry data with sensor metadata |
| `raw_geographic_regions` | `erp` | Regional reference data |
| `raw_surface_finishes` | `erp` | Finish options catalog |
| `raw_quotes` | `quoting_engine` | Raw quote events from the instant quoting engine |
| `raw_orders` | `erp` | Raw order transactions |
| `raw_order_line_items` | `erp` | Raw line-item detail |
| `raw_quality_inspections` | `iot_sensors` | Raw QC measurement events from CMMs and inspection equipment |
| `raw_supplier_performance` | `supplier_portal` | Raw monthly supplier scorecards |
| `raw_pricing_signals` | `quoting_engine` | Raw pricing data points from the AI pricing engine |
| `raw_platform_events` | `web_analytics` | Raw clickstream / user activity events |
| `raw_part_specifications` | `quoting_engine` | Raw CAD analysis output (VARIANT — nested JSON) |
| `raw_supplier_capabilities` | `supplier_portal` | Raw capability self-assessments (VARIANT — nested JSON) |
| `raw_quote_parameters` | `quoting_engine` | Raw quoting engine computation output (VARIANT — nested JSON) |

### **2b. Silver Layer (`MANUFACTURING_DEMO.SILVER`)**

Silver is the "single source of truth" for cleaned, validated, entity-level data. Each Silver table reads from one or more Bronze tables and applies quality rules.

**Silver Design Principles:**
* **Schema enforcement** — correct data types, NOT NULL constraints where appropriate.
* **Deduplication** — use `ROW_NUMBER() OVER (PARTITION BY <business_key> ORDER BY _ingested_at DESC)` to keep the latest version of each record.
* **Standardization** — normalize date formats to `DATE`/`TIMESTAMP_NTZ`, uppercase status codes, trim whitespace, standardize category values.
* **Data quality filtering** — remove or quarantine out-of-range values (negative prices → quarantine, quality scores > 100 → cap at 100, future dates for historical records → filter out).
* **Referential integrity validation** — only include records with valid foreign key references (e.g., quotes must reference existing buyers, suppliers, materials).
* **Add audit columns**: `_cleaned_at TIMESTAMP_NTZ`, `_quality_flags VARCHAR` (comma-separated list of issues found and fixed, e.g., `'deduped,date_format_fixed,price_capped'`).
* **VARIANT columns** — parse, validate, and retain as VARIANT but with known schema structure.
* **Quarantine table** — `SILVER.data_quarantine` captures records rejected during Bronze→Silver processing with the reason for rejection.

**Silver Tables:**

| Table | Source | Key Transformations |
|---|---|---|
| `buyers` | `bronze.raw_buyers` | Dedup on company_name+email, standardize country names, validate region_id FK |
| `suppliers` | `bronze.raw_suppliers` | Dedup on supplier_name+city, normalize certifications format, cap quality_score at 100 |
| `materials` | `bronze.raw_materials` | Cast numeric properties, validate ranges, fill missing densities |
| `manufacturing_processes` | `bronze.raw_manufacturing_processes` | Standardize process names to title case |
| `machines` | `bronze.raw_machines` | Validate tolerance ranges, cast dimensions to FLOAT |
| `geographic_regions` | `bronze.raw_geographic_regions` | Standardize country names, validate trade zones |
| `surface_finishes` | `bronze.raw_surface_finishes` | Validate roughness values, standardize category names |
| `quotes` | `bronze.raw_quotes` | Dedup on quote_id, validate all FKs, standardize status to UPPER, parse dates, filter negative prices |
| `orders` | `bronze.raw_orders` | Dedup on order_id, validate temporal consistency (quote_date < order_date < ship_date < delivery_date) |
| `order_line_items` | `bronze.raw_order_line_items` | Validate order_id FK, recalculate line_total_usd as unit_cost × quantity |
| `quality_inspections` | `bronze.raw_quality_inspections` | Validate supplier_id FK, standardize inspection_type, parse VARIANT |
| `supplier_performance` | `bronze.raw_supplier_performance` | Validate supplier_id FK, cap percentages at 100, validate date ranges |
| `pricing_analytics` | `bronze.raw_pricing_signals` | Validate FK references, standardize price_trend values |
| `platform_activity` | `bronze.raw_platform_events` | Validate buyer_id FK, standardize event_type, deduplicate on activity_id |
| `part_specifications` | `bronze.raw_part_specifications` | Validate quote_id FK, validate VARIANT schema has required keys |
| `supplier_capabilities` | `bronze.raw_supplier_capabilities` | Validate supplier_id FK, validate VARIANT schema |
| `quote_parameters` | `bronze.raw_quote_parameters` | Validate quote_id FK, validate VARIANT schema |
| `data_quarantine` | (all Bronze sources) | Rejected records with rejection reason, source table, and original payload |

### **2c. Gold Layer (`MANUFACTURING_DEMO.GOLD`)**

Gold is the business-ready layer. It contains a dimensional model (star schema) optimized for analytics, plus pre-aggregated metric tables for dashboards and Cortex Analyst.

**Gold Design Principles:**
* **Dimensional model** — fact tables surrounded by dimension tables (star schema).
* **Pre-aggregated metrics** — daily/weekly/monthly rollups that power dashboards.
* **Business logic embedded** — calculated fields, derived measures, business classifications.
* **Optimized for query patterns** — clustered on common filter/join columns.
* **Documented** — every table and column has a clear business definition.
* **Semantic model** — the Cortex Analyst YAML semantic model is built exclusively on Gold tables.

**Gold Tables — Dimensions:**

| Table | Source | Description |
|---|---|---|
| `dim_buyers` | `silver.buyers` | Buyer dimension with surrogate key, company classification, account segmentation |
| `dim_suppliers` | `silver.suppliers` | Supplier dimension with capability summary, quality tier (A/B/C/D), geographic classification |
| `dim_materials` | `silver.materials` | Material dimension with family grouping, cost tier (budget/standard/premium/exotic), machinability index |
| `dim_manufacturing_processes` | `silver.manufacturing_processes` | Process dimension with category rollup, prototyping/production flags |
| `dim_machines` | `silver.machines` | Machine dimension with age classification, capability tier |
| `dim_geographic_regions` | `silver.geographic_regions` | Region dimension with trade zone grouping, domestic/international flag |
| `dim_surface_finishes` | `silver.surface_finishes` | Finish dimension with category and cost impact tier |
| `dim_date` | (generated) | Standard date dimension: fiscal year/quarter/month/week, is_weekend, is_holiday, fiscal_period |

**Gold Tables — Facts:**

| Table | Source | Description |
|---|---|---|
| `fact_quotes` | `silver.quotes` | Quote fact with all dimension FKs, measures (unit_price, total_price, lead_time, complexity_score, ai_confidence) |
| `fact_orders` | `silver.orders` + `silver.quotes` | Order fact enriched with quote context, fulfillment metrics (days_to_ship, days_to_deliver) |
| `fact_order_line_items` | `silver.order_line_items` | Line-item fact with dimensional FKs and calculated measures |
| `fact_quality_inspections` | `silver.quality_inspections` | Inspection fact with pass/fail measures and deviation metrics |

**Gold Tables — Pre-Aggregated Analytics:**

| Table | Source | Description |
|---|---|---|
| `agg_supplier_performance_monthly` | `silver.supplier_performance` | Monthly supplier scorecard with quality tier assignment and trend direction |
| `agg_pricing_by_process_material` | `silver.pricing_analytics` | Monthly pricing trends by process×material with YoY comparison |
| `agg_platform_engagement_daily` | `silver.platform_activity` | Daily engagement metrics by buyer segment with conversion funnel |
| `agg_quote_conversion_weekly` | `silver.quotes` + `silver.orders` | Weekly quote-to-order conversion rates by process, material, region |
| `agg_manufacturing_demand_monthly` | `silver.quotes` | Monthly demand by process category with seasonal decomposition |

**Gold Tables — VARIANT/Enriched:**

| Table | Source | Description |
|---|---|---|
| `part_specifications_enriched` | `silver.part_specifications` | Part specs with flattened key metrics + full VARIANT for drill-down |
| `supplier_capabilities_enriched` | `silver.supplier_capabilities` | Capability profiles with summary scores + full VARIANT for drill-down |
| `quote_cost_analysis` | `silver.quote_parameters` | Cost breakdown analysis with extracted key metrics from VARIANT |

-----

## **3. Domain-Specific Data Requirements**

### **Data Realism**

* **Buyer Names**: Use the FAKE UDF for company names, supplemented with curated arrays of realistic engineering firms. Sprinkle in whimsical entries via manual INSERTs:
    * "Wayne Enterprises" — ordering bat-shaped brackets from Gotham, NJ
    * "Stark Industries" — ordering titanium aerospace parts from Malibu, CA
    * "Cyberdyne Systems" — ordering robotic actuator housings from Sunnyvale, CA
    * "Acme Corporation" — ordering anvil mounting brackets from Wile E. Canyon, AZ
    * "Umbrella Corporation" — ordering bioreactor housings from Raccoon City, MO
* **Supplier Names**: Realistic machine shop names from curated arrays (e.g., "Precision CNC Solutions", "Apex Metal Works", "Pacific Rim Fabrication", "Great Lakes Machining", "Black Forest Precision GmbH", "Midwest Tool & Die").
* **Materials**: Real material grades selected from curated arrays: Aluminum 6061-T6, Aluminum 7075-T6, Stainless Steel 304, Stainless Steel 316L, Titanium Grade 5 (Ti-6Al-4V), Inconel 718, Brass C360, Copper C110, ABS, Nylon 12 (PA12), PEEK, Polycarbonate, Ultem 1010, Delrin (POM).
* **Part Names**: Realistic manufactured parts from custom UDFs combining component types with modifiers (e.g., "Motor Housing Assembly", "Hydraulic Manifold Block", "PCB Enclosure Lid", "Turbine Blade Bracket").
* **Pricing**: Realistic cost structures:
    * CNC parts: $50–$5,000+
    * 3D printed parts: $10–$500
    * Injection molded: $0.50–$50/unit + $5,000–$100,000 tooling
    * Sheet metal: $20–$2,000
    * Die casting: $2–$100/unit + $10,000–$200,000 tooling
* **Lead Times**: Process-appropriate ranges:
    * CNC machining: 5–15 business days
    * 3D printing: 3–7 business days
    * Injection molding: 4–8 weeks
    * Sheet metal: 7–20 business days
    * Die casting: 6–12 weeks
* **Dates**: Use `DATEADD('day', -UNIFORM(0, 1095, RANDOM()), CURRENT_DATE())` for dates within the last 3 years. Ensure temporal consistency: `quote_date` < `order_date` < `production_start_date` < `ship_date` < `delivery_date`.

### **Analytics-Ready Anomalies**

Embed discoverable patterns for demo purposes:
* A supplier (supplier_id=42) whose quality scores decline over 6 months (story: aging equipment, predictable via trend analysis).
* A material (Titanium, material_id 9/10) with a price spike in Q3 2024 (story: supply shortage from mining disruption).
* A buyer (buyer_id=7) with unusually high quote-to-order conversion rate (story: enterprise API integration / power user).
* Seasonal ordering patterns: Q4 surge (year-end budget spend), January dip, summer lull.
* Geographic concentration shift: increasing percentage of orders routed to US suppliers over time (reshoring trend).
* A cluster of quality failures from supplier_id=42 on Titanium (story: bad material lot).
* 3D printing orders growing as percentage of total over time (story: additive adoption trend).

### **Configurability**

Use Snowflake session variables to control row counts:

```sql
SET buyers_count = 5000;
SET suppliers_count = 2000;
SET materials_count = 200;
SET manufacturing_processes_count = 15;
SET machines_count = 500;
SET geographic_regions_count = 100;
SET surface_finishes_count = 20;
SET quotes_count = 500000;
SET orders_count = 150000;
SET order_line_items_count = 400000;
SET quality_inspections_count = 120000;
SET supplier_performance_count = 50000;
SET pricing_analytics_count = 100000;
SET platform_activity_count = 300000;
SET part_specifications_count = 200000;
SET supplier_capabilities_count = 2000;
SET quote_parameters_count = 500000;
```

-----

## **4. Snowflake-Specific Technical Notes**

> These notes capture critical Snowflake behaviors discovered during implementation. Follow them to avoid compilation errors.

* **`UNIFORM()` with subqueries**: `UNIFORM(1, (SELECT COUNT(*) FROM table), RANDOM())` may fail inside CTAS with GENERATOR. Use `ABS(MOD(RANDOM(), N))` instead for array indexing and foreign key generation.
* **`ARRAY_CONSTRUCT` indexing**: Use `arr[ABS(MOD(RANDOM(), <literal_count>))]` — not `arr[UNIFORM(0, ARRAY_SIZE(arr)-1, RANDOM())]` — because `ARRAY_SIZE()` is not constant.
* **GENERATOR cross-join filtering**: `WHERE SEQ4() < some_expression` inside a GENERATOR cross-join is unreliable for controlling expansion. Prefer flat GENERATOR with post-hoc random assignment.
* **`PACKAGES` syntax**: No trailing comma — `PACKAGES = ('simplejson')` not `PACKAGES = ('simplejson',)`.
* **UDF NULL handling**: Snowflake passes `sqlNullWrapper` for SQL NULL, not Python `None`. Guard with `if not isinstance(params, dict)`.

-----

## **5. Deliverables**

Please generate the following SQL script structure:

```
/
|-- 00_setup.sql                    # Database, 3 schemas (BRONZE, SILVER, GOLD), warehouse, session variables.
|-- 01_create_udfs.sql              # FAKE() UDF and all custom Python UDFs for domain-specific data generation.
|-- 02_create_tables_bronze.sql     # DDL for all Bronze tables (raw_* with ingestion metadata columns).
|-- 03_create_tables_silver.sql     # DDL for all Silver tables (cleaned, typed, with audit columns).
|-- 04_create_tables_gold.sql       # DDL for all Gold tables (dimensions, facts, aggregates, enriched VARIANT).
|-- 05_populate_bronze.sql          # INSERT/CTAS to generate raw synthetic data into Bronze. Includes dirty data injection.
|-- 06_populate_silver.sql          # CTAS/INSERT from Bronze → Silver. Dedup, clean, validate, quarantine bad records.
|-- 07_populate_gold_dimensions.sql # CTAS from Silver → Gold dimension tables. Includes dim_date generation.
|-- 08_populate_gold_facts.sql      # CTAS from Silver → Gold fact tables with calculated measures.
|-- 09_populate_gold_aggregates.sql # CTAS from Silver/Gold → pre-aggregated analytics and enriched VARIANT tables.
|-- 10_easter_eggs.sql              # Manual INSERT statements for whimsical/fun data rows (into Bronze, then flow through).
|-- 11_query_examples.sql           # Example SELECTs: VARIANT queries, FLATTEN, cross-layer lineage, anomaly detection, aggregations.
|-- 12_semantic_model.yaml          # Snowflake Cortex Analyst semantic model built on Gold layer tables.
|-- README.md                       # Instructions on execution order, layer descriptions, anomaly guide.
```

**Script Structure:**

* Each script should be well-commented, explaining the logic for generating data for each table and how the medallion layers connect.
* Scripts should be modular and executed in numbered order to respect layer dependencies: setup → UDFs → Bronze DDL → Silver DDL → Gold DDL → Bronze data → Silver data → Gold dimensions → Gold facts → Gold aggregates → Easter eggs → Examples.
* The UDF script should be run first so all subsequent scripts can call `FAKE()` and custom UDFs.
* Use `CREATE TABLE ... AS SELECT` (CTAS) for large tables to leverage Snowflake's parallel execution.
* For VARIANT columns, demonstrate both approaches: `OBJECT_CONSTRUCT()` in SQL and Python UDFs that return complex nested JSON.
* Bronze→Silver transformations should demonstrate real data engineering patterns: ROW_NUMBER dedup, CASE-based normalization, FK validation via LEFT JOIN + WHERE IS NOT NULL, quarantine INSERT for rejected records.
* Silver→Gold transformations should demonstrate dimensional modeling: surrogate keys, derived classifications (quality tiers, cost tiers), calculated measures, date dimension joins.

**`README.md` Content:**

* A simple set of instructions:
  1. Set the target database and warehouse in `00_setup.sql`.
  2. Execute scripts in numbered order (00 through 12).
  3. Adjust session variables in `00_setup.sql` to control row counts.
  4. Example queries are in `11_query_examples.sql`.
* Medallion architecture overview with layer descriptions.
* Table listing by layer (Bronze/Silver/Gold) with row counts.
* Anomaly guide for demo storytelling.
* Cross-layer lineage diagram (text-based).

Please begin by analyzing the requirements and then generate the complete SQL project structure and code.
