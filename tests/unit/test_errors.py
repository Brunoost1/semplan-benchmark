from __future__ import annotations

from semplan.errors import ERROR_LAYERS, ErrorCode, ProjectError, ProjectErrorRecord


def test_every_error_code_has_a_layer() -> None:
    assert set(ERROR_LAYERS) == set(ErrorCode)


def test_project_error_exposes_stable_record() -> None:
    error = ProjectError(
        ErrorCode.BUDGET_EXCEEDED,
        "Budget preflight failed",
        case_id="case-001",
        run_id="run-001",
        detail={"budget_usd": "18.00"},
    )

    record = error.to_record()

    assert isinstance(record, ProjectErrorRecord)
    assert record.code is ErrorCode.BUDGET_EXCEEDED
    assert record.layer == "cost"
    assert record.retryable is False
    assert record.detail == {"budget_usd": "18.00"}
