SELECT quarter, SUM(expense_amount) AS expense_amount FROM analytics_expense_facts WHERE year = 2026 AND quarter IN (2, 3) GROUP BY quarter ORDER BY quarter ASC
