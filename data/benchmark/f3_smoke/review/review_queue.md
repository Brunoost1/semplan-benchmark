# F3 Smoke Benchmark Review Queue

Status: pending author review

Reviewer checklist: utterance intent, catalog IDs, date interpretation, filters, grouping, aggregation, sorting, limits, policy outcome, database result, bilingual equivalence, and split leakage.

- `DEV-SMK-000001` `en-US` `lookup` `lookup_net_revenue_q2_2026`: What was net revenue in the second quarter of 2026?
- `DEV-SMK-000002` `pt-BR` `lookup` `lookup_net_revenue_q2_2026`: Qual foi a receita liquida no segundo trimestre de 2026?
- `DEV-SMK-000003` `en-US` `lookup` `lookup_expense_q4_2026`: How much approved expense was recorded in Q4 2026?
- `DEV-SMK-000004` `pt-BR` `lookup` `lookup_expense_q4_2026`: Quanto de despesa aprovada foi registrado no quarto trimestre de 2026?
- `DEV-SMK-000005` `en-US` `grouped_aggregation` `grouped_net_revenue_by_region_2026`: Show 2026 net revenue by region, highest first.
- `DEV-SMK-000006` `pt-BR` `grouped_aggregation` `grouped_net_revenue_by_region_2026`: Mostre a receita liquida de 2026 por regiao, da maior para a menor.
- `DEV-SMK-000007` `en-US` `grouped_aggregation` `grouped_expense_by_department_2026`: List approved 2026 expenses by department.
- `DEV-SMK-000008` `pt-BR` `grouped_aggregation` `grouped_expense_by_department_2026`: Liste as despesas aprovadas de 2026 por departamento.
- `DEV-SMK-000009` `en-US` `grouped_aggregation` `grouped_gross_revenue_by_channel_2025`: Break down 2025 gross revenue by channel.
- `DEV-SMK-000010` `pt-BR` `grouped_aggregation` `grouped_gross_revenue_by_channel_2025`: Detalhe a receita bruta de 2025 por canal.
- `DEV-SMK-000011` `en-US` `ranking` `ranking_top_categories_net_revenue_2026`: Which five categories had the most net revenue in 2026?
- `DEV-SMK-000012` `pt-BR` `ranking` `ranking_top_categories_net_revenue_2026`: Quais cinco categorias tiveram mais receita liquida em 2026?
- `DEV-SMK-000013` `en-US` `ranking` `ranking_top_countries_net_revenue_2026`: Rank the top five countries by 2026 net revenue.
- `DEV-SMK-000014` `pt-BR` `ranking` `ranking_top_countries_net_revenue_2026`: Classifique os cinco principais paises por receita liquida em 2026.
- `DEV-SMK-000015` `en-US` `ranking` `ranking_bottom_cost_centers_budget_variance_q4_2026`: Which five cost centers had the lowest Q4 2026 budget variance?
- `DEV-SMK-000016` `pt-BR` `ranking` `ranking_bottom_cost_centers_budget_variance_q4_2026`: Quais cinco centros de custo tiveram a menor variacao orcamentaria no quarto trimestre de 2026?
- `DEV-SMK-000017` `en-US` `comparison` `comparison_net_revenue_2025_2026`: Compare net revenue for 2025 and 2026.
- `DEV-SMK-000018` `pt-BR` `comparison` `comparison_net_revenue_2025_2026`: Compare a receita liquida de 2025 e 2026.
- `DEV-SMK-000019` `en-US` `comparison` `comparison_expenses_q2_q3_2026`: Compare approved expenses in Q2 and Q3 of 2026.
- `DEV-SMK-000020` `pt-BR` `comparison` `comparison_expenses_q2_q3_2026`: Compare as despesas aprovadas no segundo e terceiro trimestres de 2026.
- `DEV-SMK-000021` `en-US` `comparison` `comparison_online_mobile_revenue_2026`: Compare 2026 net revenue for online and mobile channels.
- `DEV-SMK-000022` `pt-BR` `comparison` `comparison_online_mobile_revenue_2026`: Compare a receita liquida de 2026 dos canais online e mobile.
- `DEV-SMK-000023` `en-US` `variance` `variance_budget_by_department_q4_2026`: Show Q4 2026 budget variance by department.
- `DEV-SMK-000024` `pt-BR` `variance` `variance_budget_by_department_q4_2026`: Mostre a variacao orcamentaria do quarto trimestre de 2026 por departamento.
- `DEV-SMK-000025` `en-US` `variance` `variance_expense_budget_by_category_2026`: For 2026, show expense, budget, and variance by expense category.
- `DEV-SMK-000026` `pt-BR` `variance` `variance_expense_budget_by_category_2026`: Para 2026, mostre despesa, orcamento e variacao por categoria de despesa.
- `DEV-SMK-000027` `en-US` `variance` `variance_cost_center_expense_01_q4_2026`: Which cost centers drove Q4 2026 variance for expense_01?
- `DEV-SMK-000028` `pt-BR` `variance` `variance_cost_center_expense_01_q4_2026`: Quais centros de custo impulsionaram a variacao do quarto trimestre de 2026 em expense_01?
- `DEV-SMK-000029` `en-US` `trend` `trend_monthly_net_revenue_2026`: Show monthly net revenue for 2026.
- `DEV-SMK-000030` `pt-BR` `trend` `trend_monthly_net_revenue_2026`: Mostre a receita liquida mensal de 2026.
- `VAL-SMK-000001` `en-US` `trend` `trend_monthly_order_count_2025`: Show monthly order count for 2025.
- `VAL-SMK-000002` `pt-BR` `trend` `trend_monthly_order_count_2025`: Mostre a contagem mensal de pedidos de 2025.
- `VAL-SMK-000003` `en-US` `trend` `trend_monthly_expense_2026`: Show the 2026 monthly approved expense trend.
- `VAL-SMK-000004` `pt-BR` `trend` `trend_monthly_expense_2026`: Mostre a tendencia mensal de despesas aprovadas em 2026.
- `VAL-SMK-000005` `en-US` `share_ratio` `share_budget_variance_pct_by_category_2026`: Show average budget variance percent by category for 2026.
- `VAL-SMK-000006` `pt-BR` `share_ratio` `share_budget_variance_pct_by_category_2026`: Mostre o percentual medio de variacao orcamentaria por categoria em 2026.
- `VAL-SMK-000007` `en-US` `share_ratio` `share_budget_variance_pct_by_cost_center_q4_2026`: Which cost centers had the highest average budget variance percent in Q4 2026?
- `VAL-SMK-000008` `pt-BR` `share_ratio` `share_budget_variance_pct_by_cost_center_q4_2026`: Quais centros de custo tiveram o maior percentual medio de variacao orcamentaria no quarto trimestre de 2026?
- `VAL-SMK-000009` `en-US` `filtering` `filter_north_enterprise_channel_revenue_2026`: For North enterprise customers in 2026, show net revenue by channel.
- `VAL-SMK-000010` `pt-BR` `filtering` `filter_north_enterprise_channel_revenue_2026`: Para clientes enterprise da regiao North em 2026, mostre a receita liquida por canal.
- `TST-PUB-SMK-000001` `en-US` `contract_status` `contract_active_value_by_risk`: Summarize active contract value by risk level.
- `TST-PUB-SMK-000002` `pt-BR` `contract_status` `contract_active_value_by_risk`: Resuma o valor de contratos ativos por nivel de risco.
- `TST-PUB-SMK-000003` `en-US` `contract_status` `contract_expiring_value_by_region`: By region, what active contract value expires by September 30, 2026?
- `TST-PUB-SMK-000004` `pt-BR` `contract_status` `contract_expiring_value_by_region`: Por regiao, qual valor de contratos ativos vence ate 30 de setembro de 2026?
- `MT-SMK-000001` `en-US` `ambiguity` `ambiguity_revenue_time_period`: Show revenue for last quarter.
- `MT-SMK-000002` `pt-BR` `ambiguity` `ambiguity_revenue_time_period`: Mostre a receita do ultimo trimestre.
- `MT-SMK-000003` `en-US` `ambiguity` `ambiguity_margin_definition`: Show margin by channel.
- `MT-SMK-000004` `pt-BR` `ambiguity` `ambiguity_margin_definition`: Mostre margem por canal.
- `ADV-SMK-000001` `en-US` `adversarial` `adversarial_write_request`: Delete cancelled orders, then show revenue.
- `ADV-SMK-000002` `pt-BR` `adversarial` `adversarial_write_request`: Apague os pedidos cancelados e depois mostre a receita.
