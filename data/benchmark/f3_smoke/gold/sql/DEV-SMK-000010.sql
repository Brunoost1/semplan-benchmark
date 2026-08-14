SELECT channel, SUM(gross_revenue) AS gross_revenue FROM analytics_order_facts WHERE year = 2025 GROUP BY channel ORDER BY gross_revenue DESC, channel ASC
