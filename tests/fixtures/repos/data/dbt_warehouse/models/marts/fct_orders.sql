select order_id, customer_id, total from {{ ref('stg_orders') }}
