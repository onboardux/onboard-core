select
    date_trunc('day', o.created_at) as order_day,
    c.region,
    count(*) as order_count,
    sum(o.total_amount) as revenue_amount
from {{ ref('stg_orders') }} as o
join {{ ref('stg_customers') }} as c using (customer_id)
group by 1, 2
