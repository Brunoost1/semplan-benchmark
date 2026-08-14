SELECT department, SUM(expense_amount) AS expense_amount FROM analytics_expense_facts WHERE year = 2026 GROUP BY department ORDER BY expense_amount DESC, department ASC LIMIT 5
