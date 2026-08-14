SELECT month, SUM(net_revenue) AS net_revenue FROM analytics_order_facts WHERE year = 2026 GROUP BY month ORDER BY month ASC
