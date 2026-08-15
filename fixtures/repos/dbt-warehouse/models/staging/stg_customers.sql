select
    customer_id,
    region,
    signed_up_at
from {{ source('raw', 'customers') }}
