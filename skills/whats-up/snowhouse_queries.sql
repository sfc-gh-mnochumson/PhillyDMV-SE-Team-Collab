-- Query 1: Month-over-month revenue with year-over-year comparison
-- Last 3 calendar months (current year) vs same 3 months prior year
-- Aggregated into meaningful categories per account per month
select
    a.salesforce_account_name
    ,a.snowflake_account_name
    ,i.alias                                          as snowflake_account_alias
    ,date_trunc('month', r.general_date)              as month
    ,year(r.general_date)                             as yr
    ,to_char(date_trunc('month', r.general_date), 'Mon YYYY') as month_label
    ,sum(r.TOTAL_PRODUCT_REVENUE_LOCAL)               as total_revenue
    ,sum(r.COMPUTE_REVENUE_LOCAL)                     as compute_revenue
    ,sum(coalesce(r.AI_FUNCTIONS_REVENUE_LOCAL, 0)
       + coalesce(r.AI_SERVICES_REVENUE_LOCAL, 0)
       + coalesce(r.CORTEX_AGENTS_REVENUE_LOCAL, 0)
       + coalesce(r.CORTEX_SEARCH_REVENUE_LOCAL, 0)
       + coalesce(r.SNOWFLAKE_INTELLIGENCE_REVENUE_LOCAL, 0))  as ai_revenue
    ,sum(coalesce(r.CORTEX_CODE_CLI_REVENUE_LOCAL, 0)
       + coalesce(r.CORTEX_CODE_DESKTOP_REVENUE_LOCAL, 0)
       + coalesce(r.CORTEX_CODE_SNOWSIGHT_REVENUE_LOCAL, 0))   as coco_revenue
    ,sum(coalesce(r.SNOWPARK_CONTAINER_SERVICES_REVENUE_LOCAL, 0)) as spcs_revenue
from temp.wwash_winterfell.dim_customer_current_user c
inner join snowscience.dimensions.dim_snowflake_accounts a
    on c.salesforce_account_id = a.salesforce_account_id
inner join snowhouse_import.prod.account_etl_v i
    on a.snowflake_account_id = i.id
    and a.snowflake_deployment = i.deployment
left outer join FINANCE.CUSTOMER.SNOWFLAKE_ACCOUNT_REVENUE_ETM r
    on a.snowflake_account_id = r.snowflake_account_id
    and a.snowflake_deployment = r.snowflake_deployment
    and (
        -- current year: last 3 calendar months through today
        (r.general_date >= date_trunc('month', dateadd('month', -2, current_date))
         and r.general_date <= current_date)
        or
        -- prior year: same 3-month window one year ago
        (r.general_date >= dateadd('year', -1, date_trunc('month', dateadd('month', -2, current_date)))
         and r.general_date <= dateadd('year', -1, current_date))
    )
where c.my_accounts = 1
group by 1, 2, 3, 4, 5, 6
order by a.salesforce_account_name, month
;


select
a.salesforce_account_name
,ag.created_on
,s.parent_name || '.' || s.name || '.' || ag.name as agent_name
from temp.wwash_winterfell.dim_customer_current_user c
inner join snowscience.dimensions.dim_snowflake_accounts a
on c.salesforce_account_id = a.salesforce_account_id
inner join snowscience.live_objects.all_live_agents ag
  on a.snowflake_account_id = ag.account_id
  and a.snowflake_deployment = ag.deployment
  and ag.deleted_on is null
  and ag.created_on >= current_date() - 7
inner join snowscience.live_objects.all_live_schemas s 
  on ag.account_id = s.account_id 
  and ag.deployment = s.deployment 
  and ag.parent_id = s.id
  and s.deleted_on is null
  and s.ds = current_date()
left outer join snowscience.live_objects.all_live_agents ag_old
  on a.snowflake_account_id = ag_old.account_id
  and a.snowflake_deployment = ag_old.deployment
  and ag.name = ag_old.name
  and ag_old.deleted_on is not null
  and ag_old.created_on < current_date() - 7
where
c.my_accounts = 1
and ag_old.name is null;


with accounts as (
    select
        a.salesforce_account_name
        ,a.created_on as account_creation_date
        ,a.snowflake_account_name
        ,i.alias as snowflake_account_alias
        ,a.snowflake_account_id
        ,a.snowflake_deployment
    from temp.wwash_winterfell.dim_customer_current_user c
    inner join snowscience.dimensions.dim_snowflake_accounts a
        on c.salesforce_account_id = a.salesforce_account_id
    inner join snowhouse_import.prod.account_etl_v i
        on a.snowflake_account_id = i.id
        and a.snowflake_deployment = i.deployment
    where
        c.my_accounts = 1
)

,warehouse_summary as (
    select
        a.salesforce_account_name
        ,a.account_creation_date
        ,a.snowflake_account_name
        ,a.snowflake_account_alias
        ,w.warehouse_name
        ,min(w.usage_date)                                          as first_usage_date
        ,max(w.usage_date)                                          as last_usage_date
        ,sum(case when w.usage_date >= dateadd(day, -7, current_date) then w.credits else 0 end) as credits_last_7d
        ,sum(w.credits)                                             as credits_last_30d
        ,sum(w.credits) / nullif(datediff(day, min(w.usage_date), max(w.usage_date)) + 1, 0) as avg_credits_per_day
    from accounts a
    inner join finance.customer.warehouse_compute w
        on a.snowflake_account_id = w.snowflake_account_id
        and a.snowflake_deployment = w.snowflake_deployment
        and w.usage_date >= dateadd(day, -30, current_date)
    group by 1, 2, 3, 4, 5
    having avg_credits_per_day > 10
)

select
    salesforce_account_name
    ,account_creation_date
    ,snowflake_account_name
    ,snowflake_account_alias
    ,warehouse_name
    ,first_usage_date
    ,last_usage_date
    ,credits_last_7d
    ,credits_last_30d
    ,round(avg_credits_per_day, 2)                                  as avg_credits_per_day
    ,case
        when first_usage_date >= dateadd(day, -7, current_date)                        then 'New'
        when last_usage_date  <  dateadd(day, -7, current_date) and credits_last_30d > 0  then 'Inactive'
     end as warehouse_status
from warehouse_summary
where
    avg_credits_per_day > 10
    and (
        -- new: first ever activity is within the last 7 days
        first_usage_date >= dateadd(day, -7, current_date)
        or
        -- inactive: had prior activity but nothing in the last 7 days
        (last_usage_date < dateadd(day, -7, current_date) and credits_last_30d > 0)
    )
;


-- Query 4: Usage anomalies — yesterday spikes vs prior 7-day average
-- Flags features where yesterday's revenue jumped significantly above normal,
-- indicating a runaway job, misconfigured warehouse, or unexpected workload.
with base as (
    select
        a.salesforce_account_name
        ,a.snowflake_account_name
        ,i.alias as snowflake_account_alias
        ,r.general_date
,r.AI_FUNCTIONS_REVENUE_LOCAL
,r.AI_INFERENCE_REVENUE_LOCAL
,r.AI_SERVICES_REVENUE_LOCAL
,r.ARCHIVE_STORAGE_COLD_REVENUE_LOCAL
,r.ARCHIVE_STORAGE_COOL_REVENUE_LOCAL
,r.ARCHIVE_STORAGE_DATA_RETRIEVAL_REVENUE_LOCAL
,r.ARCHIVE_STORAGE_RETRIEVAL_FILE_PROCESSING_REVENUE_LOCAL
,r.ARCHIVE_STORAGE_WRITE_REVENUE_LOCAL
,r.AUTOMATED_REFRESH_AND_DATA_REGISTRATION_REVENUE_LOCAL
,r.BACKUP_REVENUE_LOCAL
,r.BATCH_CORTEX_SEARCH_REVENUE_LOCAL
,r.BLOCK_STORAGE_ADDITIONAL_IOPS_REVENUE_LOCAL
,r.BLOCK_STORAGE_ADDITIONAL_THROUGHPUT_REVENUE_LOCAL
,r.BLOCK_STORAGE_REVENUE_LOCAL
,r.CLOUD_SERVICES_READER_REVENUE_LOCAL
,r.CLOUD_SERVICES_REVENUE_LOCAL
,r.COMPUTE_REVENUE_LOCAL
,r.COPY_FILES_REVENUE_LOCAL
,r.CORTEX_AGENTS_REVENUE_LOCAL
,r.CORTEX_AI_GUARDRAILS_REVENUE_LOCAL
,r.CORTEX_CODE_CLI_REVENUE_LOCAL
,r.CORTEX_CODE_DESKTOP_REVENUE_LOCAL
,r.CORTEX_CODE_SNOWSIGHT_REVENUE_LOCAL
,r.CORTEX_SEARCH_REVENUE_LOCAL
,r.DAILY_STORAGE_REVENUE_LOCAL
,r.DATA_QUALITY_MONITORING_REVENUE_LOCAL
,r.DATA_TRANSFER_REVENUE_LOCAL
,r.DEPLOYMENT_REVENUE_LOCAL
,r.EGRESS_COST_OPTIMIZER_REVENUE_LOCAL
,r.FAILSAFE_RECOVERY_REVENUE_LOCAL
,r.HYBRID_TABLE_DEDICATED_STORAGE_MODE_REVENUE_LOCAL
,r.HYBRID_TABLE_REQUESTS_REVENUE_LOCAL
,r.HYBRID_TABLE_STORAGE_REVENUE_LOCAL
,r.INTERNAL_DATA_TRANSFER_REVENUE_LOCAL
,r.LOGGING_REVENUE_LOCAL
,r.MATERIALIZED_VIEW_REVENUE_LOCAL
,r.OPENFLOW_COMPUTE_BYOC_REVENUE_LOCAL
,r.OPENFLOW_COMPUTE_SNOWFLAKE_REVENUE_LOCAL
,r.OPENFLOW_ORACLE_CDC_LICENSE_REVENUE_LOCAL
,r.OPENFLOW_ORACLE_CDC_SUPPORT_MAINTENANCE_REVENUE_LOCAL
,r.ORGANIZATION_USAGE_REVENUE_LOCAL
,r.OUTBOUND_PRIVATELINK_DATA_PROCESSED_REVENUE_LOCAL
,r.OUTBOUND_PRIVATELINK_ENDPOINT_REVENUE_LOCAL
,r.POSTGRES_COMPUTE_HA_REVENUE_LOCAL
,r.POSTGRES_COMPUTE_REVENUE_LOCAL
,r.POSTGRES_STORAGE_HA_REVENUE_LOCAL
,r.POSTGRES_STORAGE_REVENUE_LOCAL
,r.PRIORITY_SUPPORT_REVENUE_LOCAL
,r.QUERY_ACCELERATION_REVENUE_LOCAL
,r.RECLUSTERING_REVENUE_LOCAL
,r.REPLICATION_REVENUE_LOCAL
,r.SEARCH_OPTIMIZATION_REVENUE_LOCAL
,r.SENSITIVE_DATA_CLASSIFICATION_REVENUE_LOCAL
,r.SERVERLESS_ALERTS_REVENUE_LOCAL
,r.SERVERLESS_TASKS_FLEX_REVENUE_LOCAL
,r.SERVERLESS_TASK_REVENUE_LOCAL
,r.SNAPSHOT_REVENUE_LOCAL
,r.SNOWFLAKE_APP_RUNTIME_REVENUE_LOCAL
,r.SNOWFLAKE_INTELLIGENCE_REVENUE_LOCAL
,r.SNOWPARK_CONTAINER_SERVICES_REVENUE_LOCAL
,r.SNOWPIPE_REVENUE_LOCAL
,r.SNOWPIPE_STREAMING_REVENUE_LOCAL
,r.SNOWWORK_REVENUE_LOCAL
,r.STORAGE_LIFECYCLE_POLICY_EXECUTION_REVENUE_LOCAL
,r.STORAGE_REQUEST_REVENUE_LOCAL
,r.TABLE_OPTIMIZATION_REVENUE_LOCAL
,r.TELEMETRY_DATA_INGEST_REVENUE_LOCAL
,r.TOTAL_COMPUTE_REVENUE_LOCAL
,r.TRUST_CENTER_REVENUE_LOCAL
    from temp.wwash_winterfell.dim_customer_current_user c
    inner join snowscience.dimensions.dim_snowflake_accounts a
        on c.salesforce_account_id = a.salesforce_account_id
    inner join snowhouse_import.prod.account_etl_v i
        on a.snowflake_account_id = i.id
        and a.snowflake_deployment = i.deployment
    left outer join FINANCE.CUSTOMER.SNOWFLAKE_ACCOUNT_REVENUE_ETM r
        on a.snowflake_account_id = r.snowflake_account_id
        and a.snowflake_deployment = r.snowflake_deployment
        -- pull 14 days so each of the last 7 days has 7 prior days to average against
        and r.general_date between current_date - 14 and current_date - 1
    where c.my_accounts = 1
)

,unpivoted as (
    select salesforce_account_name, snowflake_account_name, snowflake_account_alias,
           general_date, feature, revenue
    from base
    unpivot (revenue for feature in (AI_FUNCTIONS_REVENUE_LOCAL
,AI_INFERENCE_REVENUE_LOCAL
,AI_SERVICES_REVENUE_LOCAL
,ARCHIVE_STORAGE_COLD_REVENUE_LOCAL
,ARCHIVE_STORAGE_COOL_REVENUE_LOCAL
,ARCHIVE_STORAGE_DATA_RETRIEVAL_REVENUE_LOCAL
,ARCHIVE_STORAGE_RETRIEVAL_FILE_PROCESSING_REVENUE_LOCAL
,ARCHIVE_STORAGE_WRITE_REVENUE_LOCAL
,AUTOMATED_REFRESH_AND_DATA_REGISTRATION_REVENUE_LOCAL
,BACKUP_REVENUE_LOCAL
,BATCH_CORTEX_SEARCH_REVENUE_LOCAL
,BLOCK_STORAGE_ADDITIONAL_IOPS_REVENUE_LOCAL
,BLOCK_STORAGE_ADDITIONAL_THROUGHPUT_REVENUE_LOCAL
,BLOCK_STORAGE_REVENUE_LOCAL
,CLOUD_SERVICES_READER_REVENUE_LOCAL
,CLOUD_SERVICES_REVENUE_LOCAL
,COMPUTE_REVENUE_LOCAL
,COPY_FILES_REVENUE_LOCAL
,CORTEX_AGENTS_REVENUE_LOCAL
,CORTEX_AI_GUARDRAILS_REVENUE_LOCAL
,CORTEX_CODE_CLI_REVENUE_LOCAL
,CORTEX_CODE_DESKTOP_REVENUE_LOCAL
,CORTEX_CODE_SNOWSIGHT_REVENUE_LOCAL
,CORTEX_SEARCH_REVENUE_LOCAL
,DAILY_STORAGE_REVENUE_LOCAL
,DATA_QUALITY_MONITORING_REVENUE_LOCAL
,DATA_TRANSFER_REVENUE_LOCAL
,DEPLOYMENT_REVENUE_LOCAL
,EGRESS_COST_OPTIMIZER_REVENUE_LOCAL
,FAILSAFE_RECOVERY_REVENUE_LOCAL
,HYBRID_TABLE_DEDICATED_STORAGE_MODE_REVENUE_LOCAL
,HYBRID_TABLE_REQUESTS_REVENUE_LOCAL
,HYBRID_TABLE_STORAGE_REVENUE_LOCAL
,INTERNAL_DATA_TRANSFER_REVENUE_LOCAL
,LOGGING_REVENUE_LOCAL
,MATERIALIZED_VIEW_REVENUE_LOCAL
,OPENFLOW_COMPUTE_BYOC_REVENUE_LOCAL
,OPENFLOW_COMPUTE_SNOWFLAKE_REVENUE_LOCAL
,OPENFLOW_ORACLE_CDC_LICENSE_REVENUE_LOCAL
,OPENFLOW_ORACLE_CDC_SUPPORT_MAINTENANCE_REVENUE_LOCAL
,ORGANIZATION_USAGE_REVENUE_LOCAL
,OUTBOUND_PRIVATELINK_DATA_PROCESSED_REVENUE_LOCAL
,OUTBOUND_PRIVATELINK_ENDPOINT_REVENUE_LOCAL
,POSTGRES_COMPUTE_HA_REVENUE_LOCAL
,POSTGRES_COMPUTE_REVENUE_LOCAL
,POSTGRES_STORAGE_HA_REVENUE_LOCAL
,POSTGRES_STORAGE_REVENUE_LOCAL
,PRIORITY_SUPPORT_REVENUE_LOCAL
,QUERY_ACCELERATION_REVENUE_LOCAL
,RECLUSTERING_REVENUE_LOCAL
,REPLICATION_REVENUE_LOCAL
,SEARCH_OPTIMIZATION_REVENUE_LOCAL
,SENSITIVE_DATA_CLASSIFICATION_REVENUE_LOCAL
,SERVERLESS_ALERTS_REVENUE_LOCAL
,SERVERLESS_TASKS_FLEX_REVENUE_LOCAL
,SERVERLESS_TASK_REVENUE_LOCAL
,SNAPSHOT_REVENUE_LOCAL
,SNOWFLAKE_APP_RUNTIME_REVENUE_LOCAL
,SNOWFLAKE_INTELLIGENCE_REVENUE_LOCAL
,SNOWPARK_CONTAINER_SERVICES_REVENUE_LOCAL
,SNOWPIPE_REVENUE_LOCAL
,SNOWPIPE_STREAMING_REVENUE_LOCAL
,SNOWWORK_REVENUE_LOCAL
,STORAGE_LIFECYCLE_POLICY_EXECUTION_REVENUE_LOCAL
,STORAGE_REQUEST_REVENUE_LOCAL
,TABLE_OPTIMIZATION_REVENUE_LOCAL
,TELEMETRY_DATA_INGEST_REVENUE_LOCAL
,TOTAL_COMPUTE_REVENUE_LOCAL
,TRUST_CENTER_REVENUE_LOCAL
    ))
    where general_date is not null
)

,rolling as (
    select
        salesforce_account_name
        ,snowflake_account_name
        ,snowflake_account_alias
        ,general_date                                as spike_date
        ,feature
        ,revenue                                     as spike_value
        -- rolling avg of the 7 days immediately before this date
        ,avg(revenue) over (
            partition by salesforce_account_name, snowflake_account_name, feature
            order by general_date
            rows between 7 preceding and 1 preceding
        )                                            as rolling_avg_prior_7d
    from unpivoted
)

select
    salesforce_account_name
    ,snowflake_account_name
    ,snowflake_account_alias
    ,spike_date
    ,replace(feature, '_REVENUE_LOCAL', '')          as feature
    ,round(coalesce(rolling_avg_prior_7d, 0), 2)     as avg_prior_7d
    ,round(spike_value, 2)                            as spike_value
    ,round(spike_value - coalesce(rolling_avg_prior_7d, 0), 2) as spike_amount
    ,round(spike_value / nullif(rolling_avg_prior_7d, 0), 1)   as spike_ratio
from rolling
where spike_date >= current_date - 7                 -- only flag the last 7 days
  and spike_value > coalesce(rolling_avg_prior_7d, 0) + 100
  and spike_value > coalesce(rolling_avg_prior_7d, 0) * 2
  and spike_value > 50
order by
    spike_date desc
    ,spike_amount desc
;

