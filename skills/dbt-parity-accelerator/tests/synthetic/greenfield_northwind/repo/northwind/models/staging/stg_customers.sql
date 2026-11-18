select
    id as customer_id,
    name as customer_name
from {{ source('crm', 'customers') }}
