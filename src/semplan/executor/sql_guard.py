"""AST-based SQL safety checks for evaluated raw SQL paths."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlglot import exp, parse
from sqlglot.errors import ParseError

from semplan.errors import ErrorCode, ProjectError

DEFAULT_ALLOWED_TABLES = frozenset(
    {
        "analytics_order_facts",
        "analytics_expense_facts",
        "analytics_budget_facts",
        "analytics_contract_facts",
        "dim_calendar",
        "dim_customers",
        "dim_products",
        "dim_departments",
        "dim_cost_centers",
        "dim_suppliers",
    }
)

DEFAULT_ALLOWED_COLUMNS = frozenset(
    {
        "active",
        "active_contract_value",
        "active_customer_id",
        "annual_value",
        "active_customer_count",
        "average_order_value",
        "brand",
        "budget_amount",
        "budget_variance",
        "budget_variance_pct",
        "category",
        "channel",
        "code",
        "contract_id",
        "contract_risk",
        "cost_center_id",
        "country_code",
        "cost_center",
        "customer_id",
        "customer_segment",
        "date",
        "department",
        "department_id",
        "end_date",
        "expense_id",
        "expense_amount",
        "expense_category",
        "expense_date",
        "gross_revenue",
        "is_business_day",
        "iso_week",
        "method",
        "month",
        "month_name",
        "name",
        "net_revenue",
        "order_date",
        "order_id",
        "order_count",
        "payment_method",
        "product_id",
        "quarter",
        "region",
        "risk_level",
        "segment",
        "start_date",
        "status",
        "subcategory",
        "supplier_id",
        "supplier_category",
        "unit_cost",
        "week",
        "weekday",
        "year",
        "contribution_margin",
        "contribution_margin_pct",
    }
)

DEFAULT_ALLOWED_FUNCTIONS = frozenset(
    {"avg", "cast", "count", "date_trunc", "extract", "max", "min", "nullif", "sum"}
)

PROHIBITED_EXPRESSIONS: tuple[type[exp.Expression], ...] = (
    exp.Alter,
    exp.Command,
    exp.Commit,
    exp.Copy,
    exp.Create,
    exp.Delete,
    exp.Drop,
    exp.Grant,
    exp.Insert,
    exp.Merge,
    exp.Rollback,
    exp.Set,
    exp.Transaction,
    exp.Update,
    exp.Use,
)


@dataclass(frozen=True)
class SqlPolicy:
    allowed_tables: frozenset[str] = DEFAULT_ALLOWED_TABLES
    allowed_columns: frozenset[str] = DEFAULT_ALLOWED_COLUMNS
    allowed_functions: frozenset[str] = DEFAULT_ALLOWED_FUNCTIONS
    max_tables: int = 4
    max_joins: int = 4
    max_select_expressions: int = 16
    max_ast_nodes: int = 200
    max_ast_depth: int = 32
    denied_tokens: tuple[str, ...] = field(default=("--", "/*", "*/"))


@dataclass(frozen=True)
class GuardedSql:
    sql: str
    normalized_sql: str
    tables: tuple[str, ...]
    columns: tuple[str, ...]


DEFAULT_SQL_POLICY = SqlPolicy()


def _policy_error(message: str, *, detail: dict[str, object] | None = None) -> ProjectError:
    return ProjectError(
        ErrorCode.POLICY_VIOLATION,
        message,
        detail=detail or {},
    )


def _parse_error(sql: str, reason: str) -> ProjectError:
    return ProjectError(
        ErrorCode.SQL_PARSE_FAILED,
        "SQL parse failed",
        detail={"reason": reason, "sql_length": len(sql)},
    )


def _max_depth(expression: exp.Expression) -> int:
    child_depths = [
        _max_depth(child)
        for child in expression.iter_expressions()
        if isinstance(child, exp.Expression)
    ]
    return 1 + (max(child_depths) if child_depths else 0)


def validate_select_sql(sql: str, policy: SqlPolicy = DEFAULT_SQL_POLICY) -> GuardedSql:
    """Validate one SELECT statement against read-only SQL policy."""

    stripped = sql.strip()
    if not stripped:
        raise _parse_error(sql, "empty SQL")
    lowered = stripped.lower()
    for token in policy.denied_tokens:
        if token in lowered:
            raise _policy_error("SQL comments are prohibited", detail={"token": token})

    try:
        statements = parse(stripped, read="postgres")
    except ParseError as exc:
        raise _parse_error(sql, str(exc)) from exc

    if len(statements) != 1:
        raise _policy_error(
            "Exactly one SQL statement is allowed",
            detail={"statement_count": len(statements)},
        )

    statement = statements[0]
    if not isinstance(statement, exp.Select):
        raise _policy_error("Only SELECT statements are allowed")

    ast_nodes = list(statement.walk())
    if len(ast_nodes) > policy.max_ast_nodes:
        raise _policy_error("SQL AST exceeds complexity limit", detail={"nodes": len(ast_nodes)})
    if _max_depth(statement) > policy.max_ast_depth:
        raise _policy_error("SQL AST exceeds depth limit")

    for node in ast_nodes:
        if isinstance(node, PROHIBITED_EXPRESSIONS):
            raise _policy_error(
                "Prohibited SQL construct",
                detail={"construct": type(node).__name__},
            )

    tables = tuple(sorted({table.name for table in statement.find_all(exp.Table)}))
    if len(tables) > policy.max_tables:
        raise _policy_error("Too many tables referenced", detail={"tables": tables})
    unknown_tables = sorted(set(tables).difference(policy.allowed_tables))
    if unknown_tables:
        raise _policy_error(
            "SQL references non-allowlisted tables", detail={"tables": unknown_tables}
        )

    joins = list(statement.find_all(exp.Join))
    if len(joins) > policy.max_joins:
        raise _policy_error("Too many joins referenced", detail={"joins": len(joins)})

    select_expressions = statement.expressions
    if len(select_expressions) > policy.max_select_expressions:
        raise _policy_error(
            "Too many select expressions",
            detail={"select_expressions": len(select_expressions)},
        )

    columns = tuple(sorted({column.name for column in statement.find_all(exp.Column)}))
    unknown_columns = sorted(set(columns).difference(policy.allowed_columns))
    if unknown_columns:
        raise _policy_error(
            "SQL references non-allowlisted columns",
            detail={"columns": unknown_columns},
        )

    for star in statement.find_all(exp.Star):
        raise _policy_error("SELECT * is prohibited", detail={"construct": star.sql()})

    functions = {
        function.key.lower()
        for function in statement.find_all(exp.Func)
        if not isinstance(function, exp.Connector)
    }
    unknown_functions = sorted(functions.difference(policy.allowed_functions))
    if unknown_functions:
        raise _policy_error(
            "SQL references non-allowlisted functions",
            detail={"functions": unknown_functions},
        )

    return GuardedSql(
        sql=stripped,
        normalized_sql=statement.sql(dialect="postgres"),
        tables=tables,
        columns=columns,
    )
