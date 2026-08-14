from __future__ import annotations

from pathlib import Path
from types import TracebackType
from typing import Any

import pytest
from sqlalchemy.exc import DBAPIError

from semplan.approaches.direct_sql import DirectSqlRunner, execute_direct_sql
from semplan.benchmark.validator import load_benchmark_cases
from semplan.catalog import load_catalog
from semplan.contracts import ResultOutcome
from semplan.errors import ErrorCode, ProjectError
from semplan.prompts import PromptRegistry
from semplan.providers import FakeProvider

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_DIR = PROJECT_ROOT / "data/benchmark/f3_smoke"


def test_direct_sql_rejects_unsafe_sql_before_database(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_create_engine(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("database should not be touched")

    monkeypatch.setattr("semplan.approaches.direct_sql.runner.create_engine", fail_create_engine)

    with pytest.raises(ProjectError):
        execute_direct_sql("DROP TABLE analytics_order_facts")


def test_direct_sql_runner_handles_cannot_answer_without_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_execute(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("blocked direct-SQL case should not execute")

    monkeypatch.setattr("semplan.approaches.direct_sql.runner.execute_direct_sql", fail_execute)
    case = next(
        case for case in load_benchmark_cases(BENCHMARK_DIR) if case.case_id.startswith("ADV")
    )
    provider = FakeProvider(
        {
            case.case_id: {
                "schema_version": "1.0",
                "sql": None,
                "assumptions": [],
                "cannot_answer": True,
                "reason_code": "WRITE_OPERATION",
            }
        }
    )
    runner = DirectSqlRunner(
        provider=provider,
        catalog=load_catalog(PROJECT_ROOT / "catalog"),
        prompt_registry=PromptRegistry.load(PROJECT_ROOT / "prompts"),
    )

    result = runner.run_case(case)

    assert result.outcome is ResultOutcome.OUT_OF_SCOPE
    assert result.execution is None


def test_direct_sql_runner_wraps_invalid_provider_payload() -> None:
    case = next(
        case for case in load_benchmark_cases(BENCHMARK_DIR) if case.expected_policy == "ALLOW"
    )
    provider = FakeProvider(
        {
            case.case_id: {
                "schema_version": "1.0",
                "sql": None,
                "assumptions": [],
                "cannot_answer": False,
                "reason_code": None,
            }
        }
    )
    runner = DirectSqlRunner(
        provider=provider,
        catalog=load_catalog(PROJECT_ROOT / "catalog"),
        prompt_registry=PromptRegistry.load(PROJECT_ROOT / "prompts"),
    )

    with pytest.raises(ProjectError) as exc_info:
        runner.run_case(case)

    assert exc_info.value.to_record().code is ErrorCode.OUTPUT_SCHEMA_INVALID


class FakeMappings:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows

    def fetchmany(self, size: int) -> list[dict[str, object]]:
        return self.rows[:size]


class FakeExecutionResult:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows

    def mappings(self) -> FakeMappings:
        return FakeMappings(self.rows)


class FakeConnection:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.statements: list[str] = []

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

    def execute(self, statement: Any) -> FakeExecutionResult:
        assert "SELECT" in str(statement)
        return FakeExecutionResult(self.rows)


class FailingConnection(FakeConnection):
    def __init__(self, reason: str) -> None:
        super().__init__([])
        self.reason = reason

    def execute(self, statement: Any) -> FakeExecutionResult:
        assert "SELECT" in str(statement)
        raise DBAPIError.instance(
            statement="SELECT 1",
            params={},
            orig=Exception(self.reason),
            dbapi_base_err=Exception,
        )


class FakeEngine:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.connection = FakeConnection(rows)
        self.disposed = False

    def connect(self) -> FakeConnection:
        return self.connection

    def dispose(self) -> None:
        self.disposed = True


def test_execute_direct_sql_uses_readonly_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = FakeEngine([{"one": 1}])
    monkeypatch.setattr("semplan.approaches.direct_sql.runner.create_engine", lambda *_args: engine)

    result = execute_direct_sql("SELECT 1 AS one", database_url="fake")

    assert result.rows == [{"one": 1}]
    assert "SET TRANSACTION READ ONLY" in engine.connection.statements
    assert engine.disposed


def test_execute_direct_sql_enforces_row_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [{"one": index} for index in range(3)]
    monkeypatch.setattr(
        "semplan.approaches.direct_sql.runner.create_engine",
        lambda *_args: FakeEngine(rows),
    )

    with pytest.raises(ProjectError):
        execute_direct_sql("SELECT 1 AS one", database_url="fake", row_cap=2)


def test_execute_direct_sql_wraps_database_type_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = FakeEngine([])
    engine.connection = FailingConnection("operator does not exist: integer = character varying")
    monkeypatch.setattr("semplan.approaches.direct_sql.runner.create_engine", lambda *_args: engine)

    with pytest.raises(ProjectError) as exc_info:
        execute_direct_sql("SELECT 1 AS one", database_url="fake")

    assert exc_info.value.to_record().code is ErrorCode.EXECUTION_FAILED
    assert engine.disposed


def test_execute_direct_sql_preserves_timeout_classification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = FakeEngine([])
    engine.connection = FailingConnection("statement timeout")
    monkeypatch.setattr("semplan.approaches.direct_sql.runner.create_engine", lambda *_args: engine)

    with pytest.raises(ProjectError) as exc_info:
        execute_direct_sql("SELECT 1 AS one", database_url="fake")

    assert exc_info.value.to_record().code is ErrorCode.EXECUTION_TIMEOUT
    assert engine.disposed
