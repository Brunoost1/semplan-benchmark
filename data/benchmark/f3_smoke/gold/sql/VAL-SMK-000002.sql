SELECT month, COUNT(DISTINCT order_id) AS order_count FROM analytics_order_facts WHERE year = 2025 GROUP BY month ORDER BY month ASC
