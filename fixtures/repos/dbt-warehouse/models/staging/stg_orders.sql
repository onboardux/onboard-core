select
    order_id,
    customer_id,
    status,
    total_amount,
    created_at
from {{ source('raw', 'orders') }}
qualify row_number() over (partition by order_id order by ingested_at desc) = 1
