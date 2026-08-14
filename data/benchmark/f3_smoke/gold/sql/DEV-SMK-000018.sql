SELECT year, SUM(net_revenue) AS net_revenue FROM analytics_order_facts WHERE year IN (2025, 2026) GROUP BY year ORDER BY year ASC
