"""Governed SQLAlchemy execution for normalized semantic plans."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy import create_engine
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import DBAPIError

from semplan.catalog.models import Catalog, MetricEntry
from semplan.contracts import (
    Aggregation,
    Direction,
    ExecutionPolicy,
    Operator,
    ResultOutcome,
    ScalarValue,
    SemanticPlanEnvelope,
)
from semplan.data_generation.writer import canonical_json
from semplan.db import readonly_database_url
from semplan.errors import ErrorCode, ProjectError
from semplan.evaluation import canonicalize_row
from semplan.executor.sql_guard import validate_select_sql

DIMENSION_COLUMN_OVERRIDES = {
    "country": "country_code",
    "customer_segment": "customer_segment",
    "supplier": "supplier_id",
    "product": "product_id",
}
GOVERNED_FILTER_COLUMNS = frozenset({"end_date", "start_date", "status"})
DATE_COLUMNS = frozenset({"date", "end_date", "order_date", "expense_date", "start_date"})
INTEGER_COLUMNS = frozenset({"year", "quarter", "month", "week"})
BUDGET_VIEW_METRICS = frozenset(
    {"expense_amount", "budget_amount", "budget_variance", "budget_variance_pct"}
)
BUDGET_NATIVE_METRICS = frozenset({"budget_amount", "budget_variance", "budget_variance_pct"})


@dataclass(frozen=True)
class CompiledSemanticQuery:
    sql: str
    guard_sql: str
    bind_params: dict[str, object]
    sql_sha256: str
    statement: Any | None = None


@dataclass(frozen=True)
class SemanticExecutionResult:
    outcome: ResultOutcome
    rows: list[dict[str, ScalarValue]]
    units: dict[str, str]
    compiled_query: CompiledSemanticQuery
    row_count: int


def compile_semantic_plan(plan: SemanticPlanEnvelope, catalog: Catalog) -> CompiledSemanticQuery:
    """Compile a validated semantic plan using SQLAlchemy Core expressions."""

    if plan.execution.policy is not ExecutionPolicy.READ_ONLY:
        raise ProjectError(ErrorCode.POLICY_VIOLATION, "Only read-only plans may execute")
    if plan.limit is not None and plan.limit > plan.execution.max_rows:
        raise ProjectError(
            ErrorCode.EXECUTION_ROW_LIMIT,
            "Plan limit exceeds execution row cap",
            detail={"limit": plan.limit, "max_rows": plan.execution.max_rows},
        )

    metric_entries = [_metric(metric.id, catalog) for metric in plan.metric_specs]
    view = _view_for_metrics([metric.id for metric in metric_entries], metric_entries)
    table = _semantic_table(view)

    dimension_columns = [
        _dimension_expression(dimension.id, table, catalog) for dimension in plan.dimension_specs
    ]
    metric_columns = {
        metric.id: _metric_expression(metric.id, table, catalog).label(metric.id)
        for metric in plan.metric_specs
    }
    statement = sa.select(*dimension_columns, *metric_columns.values()).select_from(table)

    predicates = [
        _predicate_expression(leaf.field, leaf.operator, leaf.value, table, catalog)
        for leaf in _predicate_leaves(plan)
    ]
    if predicates:
        statement = statement.where(sa.and_(*predicates))
    if dimension_columns:
        statement = statement.group_by(*dimension_columns)

    order_expressions = _order_expressions(plan, table, catalog, metric_columns)
    if order_expressions:
        statement = statement.order_by(*order_expressions)
    row_cap = plan.limit or plan.execution.max_rows
    statement = statement.limit(row_cap)

    compiled = statement.compile(dialect=postgresql.dialect())  # type: ignore[no-untyped-call]
    guard_sql = str(
        statement.compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": True},
        )
    )
    validate_select_sql(guard_sql)
    sql = str(compiled)
    bind_params = _jsonable_params(dict(compiled.params))
    sql_hash = (
        "sha256:"
        + hashlib.sha256(
            canonical_json({"sql": sql, "params": bind_params}).encode("utf-8")
        ).hexdigest()
    )
    return CompiledSemanticQuery(
        sql=sql,
        guard_sql=guard_sql,
        bind_params=dict(compiled.params),
        sql_sha256=sql_hash,
        statement=statement,
    )


def execute_semantic_plan(
    plan: SemanticPlanEnvelope,
    catalog: Catalog,
    *,
    database_url: str | None = None,
    statement_timeout_ms: int = 5000,
) -> SemanticExecutionResult:
    compiled_query = compile_semantic_plan(plan, catalog)
    units = {metric.id: _metric(metric.id, catalog).unit for metric in plan.metric_specs}
    engine = create_engine(database_url or readonly_database_url())
    try:
        with engine.connect() as connection:
            with connection.begin():
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                connection.exec_driver_sql(
                    f"SET LOCAL statement_timeout = '{statement_timeout_ms}ms'"
                )
                if compiled_query.statement is None:
                    raise ProjectError(ErrorCode.CFG_INVALID, "Compiled query is missing statement")
                result = connection.execute(compiled_query.statement)
                rows = [canonicalize_row(dict(row), units) for row in result.mappings().fetchall()]
    except DBAPIError as exc:
        if "statement timeout" in str(exc).lower():
            raise ProjectError(ErrorCode.EXECUTION_TIMEOUT, "Semantic execution timed out") from exc
        raise ProjectError(
            ErrorCode.EXECUTION_FAILED,
            "Semantic execution failed",
            detail={"error_type": type(exc).__name__},
        ) from exc
    finally:
        engine.dispose()

    if len(rows) > plan.execution.max_rows:
        raise ProjectError(
            ErrorCode.EXECUTION_ROW_LIMIT,
            "Execution returned more rows than allowed",
            detail={"rows": len(rows), "max_rows": plan.execution.max_rows},
        )
    return SemanticExecutionResult(
        outcome=ResultOutcome.ANSWERED,
        rows=rows,
        units=units,
        compiled_query=compiled_query,
        row_count=len(rows),
    )


def _semantic_table(view: str) -> Any:
    return sa.table(
        view,
        *[
            sa.column(column_name, _column_type(column_name))
            for column_name in sorted(_all_allowed_columns())
        ],
    )


def _all_allowed_columns() -> set[str]:
    return {
        "active_contract_value",
        "active_customer_id",
        "budget_amount",
        "budget_variance",
        "budget_variance_pct",
        "category",
        "channel",
        "contract_risk",
        "cost_center",
        "country_code",
        "customer_segment",
        "date",
        "department",
        "end_date",
        "expense_amount",
        "expense_category",
        "gross_revenue",
        "month",
        "net_revenue",
        "order_id",
        "payment_method",
        "product_id",
        "quarter",
        "region",
        "start_date",
        "status",
        "supplier_id",
        "year",
        "week",
        "contribution_margin",
    }


def _column_type(column_name: str) -> Any:
    if column_name in DATE_COLUMNS:
        return sa.Date()
    if column_name in INTEGER_COLUMNS:
        return sa.Integer()
    if column_name in {
        "active_contract_value",
        "budget_amount",
        "budget_variance",
        "budget_variance_pct",
        "contribution_margin",
        "expense_amount",
        "gross_revenue",
        "net_revenue",
    }:
        return sa.Numeric()
    return sa.Text()


def _metric(metric_id: str, catalog: Catalog) -> Any:
    try:
        return catalog.metrics[metric_id]
    except KeyError as exc:
        raise ProjectError(
            ErrorCode.CATALOG_UNKNOWN_ID,
            "Unknown metric ID",
            detail={"metric_id": metric_id},
        ) from exc


def _view_for_metrics(metric_ids: list[str], metric_entries: list[MetricEntry]) -> str:
    metric_id_set = set(metric_ids)
    if metric_id_set.intersection(BUDGET_NATIVE_METRICS):
        if metric_id_set.issubset(BUDGET_VIEW_METRICS):
            return "analytics_budget_facts"
        raise ProjectError(
            ErrorCode.CATALOG_UNKNOWN_ID,
            "Budget metrics can only be combined with budget-view metrics",
            detail={"metric_ids": sorted(metric_id_set)},
        )
    views = {metric.sql.view for metric in metric_entries}
    if len(views) != 1:
        raise ProjectError(
            ErrorCode.CATALOG_UNKNOWN_ID,
            "Plan metrics span multiple governed views",
            detail={"views": sorted(views)},
        )
    return next(iter(views))


def _metric_expression(metric_id: str, table: Any, catalog: Catalog) -> Any:
    metric = _metric(metric_id, catalog)
    if metric_id == "average_order_value":
        return sa.func.sum(table.c.net_revenue) / sa.func.nullif(
            sa.func.count(sa.distinct(table.c.order_id)), 0
        )
    if metric_id == "contribution_margin_pct":
        return sa.func.sum(table.c.contribution_margin) / sa.func.nullif(
            sa.func.sum(table.c.net_revenue), 0
        )
    if metric_id == "budget_variance_pct":
        return sa.func.avg(table.c.budget_variance_pct)
    column = getattr(table.c, metric.sql.expression)
    if metric.aggregation is Aggregation.COUNT_DISTINCT:
        return sa.func.count(sa.distinct(column))
    if metric.aggregation in {Aggregation.SUM, Aggregation.DERIVED}:
        return sa.func.sum(column)
    if metric.aggregation is Aggregation.RATIO:
        return sa.func.avg(column)
    raise ProjectError(
        ErrorCode.CATALOG_UNKNOWN_ID,
        "Unsupported metric aggregation",
        detail={"metric_id": metric_id, "aggregation": metric.aggregation.value},
    )


def _dimension_expression(dimension_id: str, table: Any, catalog: Catalog) -> Any:
    if dimension_id not in catalog.dimensions:
        raise ProjectError(
            ErrorCode.CATALOG_UNKNOWN_ID,
            "Unknown dimension ID",
            detail={"dimension_id": dimension_id},
        )
    return getattr(table.c, _dimension_column_name(dimension_id, catalog))


def _dimension_column_name(dimension_id: str, catalog: Catalog) -> str:
    if dimension_id in GOVERNED_FILTER_COLUMNS:
        return dimension_id
    if dimension_id in DIMENSION_COLUMN_OVERRIDES:
        return DIMENSION_COLUMN_OVERRIDES[dimension_id]
    dimension = catalog.dimensions[dimension_id]
    if dimension.source.column == "code" and dimension_id == "cost_center":
        return "cost_center"
    if dimension.source.column == "name" and dimension_id == "department":
        return "department"
    if dimension.source.column == "method" and dimension_id == "payment_method":
        return "payment_method"
    if dimension.source.column == "risk_level" and dimension_id == "contract_risk":
        return "contract_risk"
    if dimension.source.column == "iso_week":
        return "week"
    return dimension.source.column


def _predicate_leaves(plan: SemanticPlanEnvelope) -> list[Any]:
    def walk(node: Any) -> list[Any]:
        if getattr(node, "type", None) == "predicate":
            return [node]
        leaves: list[Any] = []
        for child in node.children:
            leaves.extend(walk(child))
        return leaves

    return walk(plan.predicate_tree)


def _predicate_expression(
    field: str,
    operator: Operator,
    value: object,
    table: Any,
    catalog: Catalog,
) -> Any:
    column = getattr(table.c, _dimension_column_name(field, catalog))
    coerced_value = _coerce_filter_value(field, value)
    if operator is Operator.EQ:
        return column == coerced_value
    if operator is Operator.NE:
        return column != coerced_value
    if operator is Operator.IN:
        return column.in_(coerced_value)
    if operator is Operator.NOT_IN:
        return column.not_in(coerced_value)
    if operator is Operator.GT:
        return column > coerced_value
    if operator is Operator.GTE:
        return column >= coerced_value
    if operator is Operator.LT:
        return column < coerced_value
    if operator is Operator.LTE:
        return column <= coerced_value
    if operator is Operator.BETWEEN:
        if not isinstance(coerced_value, list) or len(coerced_value) != 2:
            raise ProjectError(ErrorCode.CATALOG_UNKNOWN_ID, "Invalid BETWEEN value")
        return column.between(coerced_value[0], coerced_value[1])
    if operator is Operator.IS_NULL:
        return column.is_(None)
    if operator is Operator.IS_NOT_NULL:
        return column.is_not(None)
    if operator is Operator.CONTAINS_CANONICAL:
        return column == coerced_value
    raise ProjectError(
        ErrorCode.CATALOG_UNKNOWN_ID,
        "Unsupported filter operator",
        detail={"operator": operator.value},
    )


def _coerce_filter_value(field: str, value: object) -> object:
    column_name = field if field in GOVERNED_FILTER_COLUMNS else None
    if column_name is None:
        return value
    if column_name in DATE_COLUMNS:
        if isinstance(value, str):
            return date.fromisoformat(value)
        if isinstance(value, list):
            return [date.fromisoformat(item) if isinstance(item, str) else item for item in value]
    return value


def _order_expressions(
    plan: SemanticPlanEnvelope,
    table: Any,
    catalog: Catalog,
    metric_columns: dict[str, Any],
) -> list[Any]:
    expressions: list[Any] = []
    sorted_fields: set[str] = set()
    for sort_spec in plan.sort_specs:
        expression = metric_columns.get(sort_spec.field)
        if expression is None:
            expression = getattr(table.c, _dimension_column_name(sort_spec.field, catalog))
        sorted_fields.add(sort_spec.field)
        expressions.append(
            expression.desc() if sort_spec.direction is Direction.DESC else expression.asc()
        )

    for dimension in plan.dimension_specs:
        if dimension.id not in sorted_fields:
            expressions.append(
                getattr(table.c, _dimension_column_name(dimension.id, catalog)).asc()
            )
    return expressions


def decimal_sum(values: list[Decimal]) -> Decimal:
    """Small pure helper retained for explicit Decimal unit coverage."""

    return sum(values, Decimal("0"))


def _jsonable_params(params: dict[str, object]) -> dict[str, object]:
    jsonable: dict[str, object] = {}
    for key, value in params.items():
        if isinstance(value, Decimal):
            jsonable[key] = str(value)
        elif isinstance(value, (datetime, date)):
            jsonable[key] = value.isoformat()
        elif isinstance(value, list):
            jsonable[key] = [
                item.isoformat()
                if isinstance(item, (datetime, date))
                else str(item)
                if isinstance(item, Decimal)
                else item
                for item in value
            ]
        else:
            jsonable[key] = value
    return jsonable
