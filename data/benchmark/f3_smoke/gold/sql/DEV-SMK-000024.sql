SELECT department, SUM(budget_variance) AS budget_variance FROM analytics_budget_facts WHERE year = 2026 AND quarter = 4 GROUP BY department ORDER BY budget_variance DESC, department ASC
