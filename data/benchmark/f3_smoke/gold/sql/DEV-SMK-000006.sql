SELECT region, SUM(net_revenue) AS net_revenue FROM analytics_order_facts WHERE year = 2026 GROUP BY region ORDER BY net_revenue DESC, region ASC LIMIT 5
