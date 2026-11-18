{{ config(materialized='incremental', unique_key='order_id') }}

select
    vbeln as order_id,
    erdat as order_date,
    netwr as net_value
from {{ source('erp', 'vbak') }}
{% if is_incremental() %}
where erdat > (select max(order_date) from {{ this }})
{% endif %}
