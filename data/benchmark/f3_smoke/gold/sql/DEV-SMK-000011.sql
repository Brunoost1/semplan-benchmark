SELECT category, SUM(net_revenue) AS net_revenue FROM analytics_order_facts WHERE year = 2026 GROUP BY category ORDER BY net_revenue DESC, category ASC LIMIT 5
