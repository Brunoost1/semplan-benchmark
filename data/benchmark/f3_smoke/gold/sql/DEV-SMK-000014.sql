SELECT country_code, SUM(net_revenue) AS net_revenue FROM analytics_order_facts WHERE year = 2026 GROUP BY country_code ORDER BY net_revenue DESC, country_code ASC LIMIT 5
