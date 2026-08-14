SELECT channel, SUM(net_revenue) AS net_revenue FROM analytics_order_facts WHERE year = 2026 AND channel IN ('online', 'mobile') GROUP BY channel ORDER BY channel ASC
