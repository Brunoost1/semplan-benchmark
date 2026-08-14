from __future__ import annotations

import pytest

from semplan.errors import ErrorCode, ProjectError
from semplan.executor import SqlPolicy, validate_select_sql


def _error_code(sql: str) -> ErrorCode:
    with pytest.raises(ProjectError) as exc_info:
        validate_select_sql(sql)
    return exc_info.value.to_record().code


def test_sql_guard_accepts_allowlisted_select() -> None:
    guarded = validate_select_sql(
        """
        SELECT region, SUM(net_revenue)
        FROM analytics_order_facts
        GROUP BY region
        ORDER BY SUM(net_revenue) DESC
        LIMIT 10
        """
    )

    assert guarded.tables == ("analytics_order_facts",)
    assert "net_revenue" in guarded.columns


def test_sql_guard_accepts_where_connectors_without_treating_them_as_functions() -> None:
    guarded = validate_select_sql(
        """
        SELECT SUM(net_revenue) AS net_revenue
        FROM analytics_order_facts
        WHERE year = 2026 AND quarter = 2
        """
    )

    assert guarded.tables == ("analytics_order_facts",)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT region FROM analytics_order_facts; SELECT region FROM dim_customers",
        "SELECT * FROM analytics_order_facts",
        "SELECT region FROM customers",
        "SELECT pg_sleep(1) FROM analytics_order_facts",
        "INSERT INTO customers(customer_id) VALUES ('00000000-0000-0000-0000-000000000000')",
        "UPDATE customers SET status = 'inactive'",
        "DELETE FROM customers",
        "DROP TABLE customers",
        "COPY customers TO STDOUT",
        "SELECT region FROM analytics_order_facts -- comment",
        "SELECT secret_column FROM analytics_order_facts",
    ],
)
def test_sql_guard_rejects_prohibited_sql(sql: str) -> None:
    assert _error_code(sql) in {ErrorCode.POLICY_VIOLATION, ErrorCode.SQL_PARSE_FAILED}


def test_sql_guard_reports_parse_failures_distinctly() -> None:
    assert _error_code("SELECT FROM") is ErrorCode.SQL_PARSE_FAILED


def test_sql_guard_rejects_empty_sql() -> None:
    assert _error_code("  ") is ErrorCode.SQL_PARSE_FAILED


def test_sql_guard_rejects_too_many_tables() -> None:
    with pytest.raises(ProjectError) as exc_info:
        validate_select_sql(
            "SELECT region FROM analytics_order_facts",
            SqlPolicy(max_tables=0),
        )

    assert exc_info.value.to_record().code is ErrorCode.POLICY_VIOLATION


def test_sql_guard_rejects_too_many_joins() -> None:
    with pytest.raises(ProjectError) as exc_info:
        validate_select_sql(
            """
            SELECT a.region
            FROM analytics_order_facts a
            JOIN dim_customers c ON a.customer_id = c.customer_id
            """,
            SqlPolicy(max_joins=0),
        )

    assert exc_info.value.to_record().code is ErrorCode.POLICY_VIOLATION


def test_sql_guard_rejects_too_many_select_expressions() -> None:
    with pytest.raises(ProjectError) as exc_info:
        validate_select_sql(
            "SELECT region, channel FROM analytics_order_facts",
            SqlPolicy(max_select_expressions=1),
        )

    assert exc_info.value.to_record().code is ErrorCode.POLICY_VIOLATION


def test_sql_guard_rejects_ast_node_limit() -> None:
    with pytest.raises(ProjectError) as exc_info:
        validate_select_sql(
            "SELECT region FROM analytics_order_facts",
            SqlPolicy(max_ast_nodes=1),
        )

    assert exc_info.value.to_record().code is ErrorCode.POLICY_VIOLATION


def test_sql_guard_rejects_ast_depth_limit() -> None:
    with pytest.raises(ProjectError) as exc_info:
        validate_select_sql(
            "SELECT region FROM analytics_order_facts",
            SqlPolicy(max_ast_depth=1),
        )

    assert exc_info.value.to_record().code is ErrorCode.POLICY_VIOLATION
