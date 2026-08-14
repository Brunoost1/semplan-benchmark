from __future__ import annotations

from pathlib import Path

from semplan.approaches.direct_sql import DirectSqlExecutionResult
from semplan.contracts import ResultOutcome
from semplan.e2e import run_free_e2e
from semplan.executor.semantic import CompiledSemanticQuery, SemanticExecutionResult

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_DIR = PROJECT_ROOT / "data/benchmark/f3_smoke"


def test_run_free_e2e_writes_report_without_network_or_paid_calls(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    def fake_execute(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return SemanticExecutionResult(
            outcome=ResultOutcome.ANSWERED,
            rows=[{"stub": "ok"}],
            units={},
            compiled_query=CompiledSemanticQuery(
                sql="SELECT 1",
                guard_sql="SELECT 1",
                bind_params={},
                sql_sha256="sha256:" + ("a" * 64),
            ),
            row_count=1,
        )

    def fake_direct_execute(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return DirectSqlExecutionResult(
            outcome=ResultOutcome.ANSWERED,
            rows=[{"stub": "ok"}],
            units={},
            compiled_query=CompiledSemanticQuery(
                sql="SELECT 1",
                guard_sql="SELECT 1",
                bind_params={},
                sql_sha256="sha256:" + ("b" * 64),
            ),
            row_count=1,
        )

    monkeypatch.setattr(
        "semplan.approaches.semantic_plan.runner.execute_semantic_plan", fake_execute
    )
    monkeypatch.setattr("semplan.approaches.tool_agent.runner.execute_semantic_plan", fake_execute)
    monkeypatch.setattr(
        "semplan.approaches.direct_sql.runner.execute_direct_sql", fake_direct_execute
    )
    monkeypatch.setattr("semplan.e2e.gold_rows_equal", lambda _rows, _answer: True)

    report = run_free_e2e(benchmark_dir=BENCHMARK_DIR, output_dir=tmp_path)

    assert report["status"] == "passed"
    assert report["case_count"] == 50
    assert report["approaches"] == ["A1", "A2", "A3", "A4"]
    assert report["paid_api_calls"] == 0
    assert report["db_execution_count"] == 220
    assert report["a4_sequence"]["status"] == "passed"
    assert (tmp_path / "report.json").is_file()
