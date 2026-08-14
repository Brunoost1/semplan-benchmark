SELECT cost_center, SUM(budget_variance) AS budget_variance FROM analytics_budget_facts WHERE year = 2026 AND quarter = 4 GROUP BY cost_center ORDER BY budget_variance ASC, cost_center ASC LIMIT 5
