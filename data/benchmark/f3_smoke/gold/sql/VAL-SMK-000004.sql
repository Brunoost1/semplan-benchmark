SELECT month, SUM(expense_amount) AS expense_amount FROM analytics_expense_facts WHERE year = 2026 GROUP BY month ORDER BY month ASC
