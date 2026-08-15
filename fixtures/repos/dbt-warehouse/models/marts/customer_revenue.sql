select
    region,
    sum(revenue_amount) as revenue_amount
from {{ ref('orders_daily') }}
group by 1
