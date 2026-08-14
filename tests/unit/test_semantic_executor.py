from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import TracebackType
from typing import Any

import pytest
from sqlalchemy.exc import DBAPIError

from semplan.catalog import load_catalog
from semplan.contracts import Intent, SemanticPlanEnvelope, SemanticRequestEnvelope
from semplan.errors import ErrorCode, ProjectError
from semplan.executor import compile_semantic_plan, decimal_sum, execute_semantic_plan
from semplan.normalizer import ReferenceContext, normalize_semantic_request


def _plan() -> SemanticPlanEnvelope:
    request = SemanticRequestEnvelope.model_validate(
        {
            "schema_version": "1.0",
            "operation": "REPLACE",
            "intent": Intent.RANKING,
            "metrics": ["net_revenue"],
            "dimensions": ["region"],
            "filters": [{"field": "year", "operator": "EQ", "value": 2026}],
            "time_grain": "year",
            "sort": [{"field": "net_revenue", "direction": "desc"}],
            "limit": 5,
            "comparison": None,
            "clarifications": [],
            "confidence": "1",
        }
    )
    result = normalize_semantic_request(
        request,
        load_catalog(Path("catalog")),
        ReferenceContext(date(2026, 8, 1), "UTC"),
    )
    assert result.plan is not None
    return result.plan


def test_compile_semantic_plan_uses_governed_sql_and_binds() -> None:
    compiled = compile_semantic_plan(_plan(), load_catalog(Path("catalog")))

    assert "analytics_order_facts" in compiled.sql
    assert "SELECT" in compiled.guard_sql
    assert compiled.bind_params
    assert compiled.sql_sha256.startswith("sha256:")


def test_compile_semantic_plan_blocks_limit_above_cap() -> None:
    plan = _plan().model_copy(update={"limit": 1001})

    with pytest.raises(ProjectError):
        compile_semantic_plan(plan, load_catalog(Path("catalog")))


def test_decimal_sum_uses_decimal_zero() -> None:
    assert decimal_sum([Decimal("1.10"), Decimal("2.20")]) == Decimal("3.30")


class FakeMappings:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[dict[str, object]]:
        return self._rows


class FakeExecutionResult:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> FakeMappings:
        return FakeMappings(self._rows)


class FakeConnection(AbstractContextManager["FakeConnection"]):
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.statements: list[str] = []
        self._rows = rows

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def begin(self) -> FakeConnection:
        return self

    def exec_driver_sql(self, statement: str) -> None:
        self.statements.append(statement)

    def execute(self, _statement: Any) -> FakeExecutionResult:
        return FakeExecutionResult(self._rows)


class FailingConnection(FakeConnection):
    def __init__(self, rows: list[dict[str, object]], reason: str = "operator mismatch") -> None:
        super().__init__(rows)
        self.reason = reason

    def execute(self, _statement: Any) -> FakeExecutionResult:
        raise DBAPIError.instance(
            statement="SELECT 1",
            params={},
            orig=Exception(self.reason),
            dbapi_base_err=Exception,
        )


class FakeEngine:
    def __init__(self, rows: list[dict[str, object]] | None = None) -> None:
        self.connection = FakeConnection(
            rows or [{"region": "North", "net_revenue": Decimal("10.00")}]
        )
        self.disposed = False

    def connect(self) -> FakeConnection:
        return self.connection

    def dispose(self) -> None:
        self.disposed = True


def test_execute_semantic_plan_uses_readonly_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_engine = FakeEngine()
    monkeypatch.setattr("semplan.executor.semantic.create_engine", lambda *_args: fake_engine)

    result = execute_semantic_plan(_plan(), load_catalog(Path("catalog")), database_url="fake")

    assert result.rows == [{"region": "North", "net_revenue": "10.00"}]
    assert "SET TRANSACTION READ ONLY" in fake_engine.connection.statements
    assert fake_engine.disposed


def test_compile_semantic_plan_covers_filter_operator_shapes() -> None:
    request = SemanticRequestEnvelope.model_validate(
        {
            "schema_version": "1.0",
            "operation": "REPLACE",
            "intent": "grouped_metric",
            "metrics": ["net_revenue"],
            "dimensions": ["channel"],
            "filters": [
                {"field": "year", "operator": "GT", "value": 2024},
                {"field": "year", "operator": "LTE", "value": 2026},
                {"field": "channel", "operator": "IN", "value": ["online", "mobile"]},
                {"field": "region", "operator": "NOT_IN", "value": ["Central"]},
                {
                    "field": "date",
                    "operator": "BETWEEN",
                    "value": ["2026-01-01", "2026-12-31"],
                },
            ],
            "time_grain": "year",
            "sort": [{"field": "channel", "direction": "asc"}],
            "limit": 10,
            "comparison": None,
            "clarifications": [],
            "confidence": "1",
        }
    )
    normalized = normalize_semantic_request(
        request,
        load_catalog(Path("catalog")),
        ReferenceContext(date(2026, 8, 1), "UTC"),
    )
    assert normalized.plan is not None

    compiled = compile_semantic_plan(normalized.plan, load_catalog(Path("catalog")))

    assert "BETWEEN" in compiled.guard_sql
    assert "NOT IN" in compiled.guard_sql


def test_compile_semantic_plan_covers_metric_view_and_filter_branches() -> None:
    catalog = load_catalog(Path("catalog"))
    examples = [
        {
            "metrics": ["order_count"],
            "dimensions": ["month"],
            "filters": [{"field": "year", "operator": "EQ", "value": 2025}],
            "sort": [{"field": "month", "direction": "asc"}],
        },
        {
            "metrics": ["average_order_value"],
            "dimensions": [],
            "filters": [{"field": "year", "operator": "GTE", "value": 2024}],
            "sort": [],
        },
        {
            "metrics": ["contribution_margin_pct"],
            "dimensions": [],
            "filters": [{"field": "year", "operator": "LT", "value": 2027}],
            "sort": [],
        },
        {
            "metrics": ["expense_amount", "budget_amount", "budget_variance"],
            "dimensions": ["expense_category"],
            "filters": [{"field": "year", "operator": "EQ", "value": 2026}],
            "sort": [{"field": "budget_variance", "direction": "desc"}],
        },
        {
            "metrics": ["budget_variance_pct"],
            "dimensions": ["cost_center"],
            "filters": [{"field": "quarter", "operator": "BETWEEN", "value": [1, 4]}],
            "sort": [{"field": "budget_variance_pct", "direction": "desc"}],
        },
        {
            "metrics": ["active_contract_value"],
            "dimensions": ["contract_risk"],
            "filters": [
                {"field": "status", "operator": "EQ", "value": "active"},
                {
                    "field": "end_date",
                    "operator": "BETWEEN",
                    "value": ["2026-08-01", "2026-09-30"],
                },
            ],
            "sort": [{"field": "active_contract_value", "direction": "desc"}],
        },
        {
            "metrics": ["net_revenue"],
            "dimensions": ["country"],
            "filters": [
                {"field": "region", "operator": "CONTAINS_CANONICAL", "value": "North"},
                {"field": "customer_segment", "operator": "NE", "value": "enterprise"},
            ],
            "sort": [{"field": "net_revenue", "direction": "desc"}],
        },
    ]

    compiled_sql = []
    for example in examples:
        request = SemanticRequestEnvelope.model_validate(
            {
                "schema_version": "1.0",
                "operation": "REPLACE",
                "intent": "grouped_metric",
                "time_grain": "year",
                "limit": 5,
                "comparison": None,
                "clarifications": [],
                "confidence": "1",
                **example,
            }
        )
        normalized = normalize_semantic_request(
            request,
            catalog,
            ReferenceContext(date(2026, 8, 1), "UTC"),
        )
        assert normalized.plan is not None
        compiled_sql.append(compile_semantic_plan(normalized.plan, catalog).guard_sql)

    assert any("count(DISTINCT" in sql for sql in compiled_sql)
    assert any("analytics_budget_facts" in sql for sql in compiled_sql)
    assert any("end_date" in sql for sql in compiled_sql)


def test_compile_semantic_plan_rejects_cross_view_metric_mix() -> None:
    catalog = load_catalog(Path("catalog"))
    request = SemanticRequestEnvelope.model_validate(
        {
            "schema_version": "1.0",
            "operation": "REPLACE",
            "intent": "grouped_metric",
            "metrics": ["budget_amount", "net_revenue"],
            "dimensions": [],
            "filters": [],
            "time_grain": None,
            "sort": [],
            "limit": 5,
            "comparison": None,
            "clarifications": [],
            "confidence": "1",
        }
    )
    normalized = normalize_semantic_request(
        request,
        catalog,
        ReferenceContext(date(2026, 8, 1), "UTC"),
    )
    assert normalized.plan is not None

    with pytest.raises(ProjectError):
        compile_semantic_plan(normalized.plan, catalog)


def test_execute_semantic_plan_blocks_rows_over_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [{"region": str(index), "net_revenue": Decimal("1.00")} for index in range(1001)]
    monkeypatch.setattr("semplan.executor.semantic.create_engine", lambda *_args: FakeEngine(rows))
    plan = _plan().model_copy(update={"limit": None})

    with pytest.raises(ProjectError):
        execute_semantic_plan(plan, load_catalog(Path("catalog")), database_url="fake")


def test_execute_semantic_plan_wraps_database_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_engine = FakeEngine()
    fake_engine.connection = FailingConnection(
        [], "operator does not exist: integer = character varying"
    )
    monkeypatch.setattr("semplan.executor.semantic.create_engine", lambda *_args: fake_engine)

    with pytest.raises(ProjectError) as exc_info:
        execute_semantic_plan(_plan(), load_catalog(Path("catalog")), database_url="fake")

    assert exc_info.value.to_record().code is ErrorCode.EXECUTION_FAILED
    assert exc_info.value.to_record().layer == "executor"
    assert fake_engine.disposed


def test_execute_semantic_plan_preserves_timeout_classification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_engine = FakeEngine()
    fake_engine.connection = FailingConnection([], "statement timeout")
    monkeypatch.setattr("semplan.executor.semantic.create_engine", lambda *_args: fake_engine)

    with pytest.raises(ProjectError) as exc_info:
        execute_semantic_plan(_plan(), load_catalog(Path("catalog")), database_url="fake")

    assert exc_info.value.to_record().code is ErrorCode.EXECUTION_TIMEOUT
    assert fake_engine.disposed
