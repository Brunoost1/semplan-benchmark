from __future__ import annotations

from datetime import UTC
from decimal import Decimal

from semplan.contracts import ResultOutcome, ToleranceSpec
from semplan.evaluation import canonical_value, canonicalize_row, rows_equal
from semplan.executor.semantic import CompiledSemanticQuery, SemanticExecutionResult
from semplan.reporting import (
    render_text,
    response_from_clarification,
    response_from_execution,
    response_from_out_of_scope,
)


def test_canonical_rows_compare_with_declared_tolerance() -> None:
    left = [{"net_revenue": "10.00"}]
    right = [{"net_revenue": "10.004"}]
    tolerances = {"net_revenue": ToleranceSpec(absolute=Decimal("0.01"), relative=Decimal("0"))}

    assert rows_equal(left, right, tolerances)
    assert rows_equal(right, left, tolerances)


def test_rendering_does_not_change_canonical_values() -> None:
    execution = SemanticExecutionResult(
        outcome=ResultOutcome.ANSWERED,
        rows=[{"region": "North", "net_revenue": "10.00"}],
        units={"net_revenue": "usd"},
        compiled_query=CompiledSemanticQuery(
            sql="SELECT 1",
            guard_sql="SELECT 1",
            bind_params={},
            sql_sha256="sha256:" + ("a" * 64),
        ),
        row_count=1,
    )

    response = response_from_execution(execution)

    assert response.rows == execution.rows
    assert render_text(response, "en-US") == "Returned 1 canonical row(s)."


def test_canonical_value_formats_dates_ratios_counts_and_unknowns() -> None:
    from datetime import date, datetime

    assert canonical_value(Decimal("0.1234561"), "ratio") == "0.123456"
    assert canonical_value(Decimal("2"), "count") == 2
    assert canonical_value(date(2026, 8, 1), None) == "2026-08-01"
    assert canonical_value(datetime(2026, 8, 1, tzinfo=UTC), None).startswith("2026-08-01T00:00:00")
    assert canonicalize_row({"net_revenue": Decimal("10.235")}, {"net_revenue": "usd"}) == {
        "net_revenue": "10.24"
    }


def test_rows_equal_detects_shape_and_tolerance_failures() -> None:
    tolerances = {"net_revenue": ToleranceSpec(absolute=Decimal("0.01"), relative=Decimal("0"))}

    assert not rows_equal([{"a": 1}], [], tolerances)
    assert not rows_equal([{"a": 1}], [{"b": 1}], tolerances)
    assert not rows_equal([{"net_revenue": "10.00"}], [{"net_revenue": "10.50"}], tolerances)


def test_clarification_and_out_of_scope_rendering() -> None:
    from semplan.contracts import (
        ClarificationOption,
        ClarificationQuestion,
        ClarificationReasonCode,
        LocalizedText,
        OutOfScopeReasonCode,
        OutOfScopeResponse,
    )

    clarification = ClarificationQuestion(
        clarification_id="clar-0123456789abcdef",
        reason_code=ClarificationReasonCode.AMBIGUOUS_METRIC,
        question=LocalizedText(
            **{"en-US": "Which metric should be used?", "pt-BR": "Qual metrica deve ser usada?"}
        ),
        options=[
            ClarificationOption(
                option_id="net_revenue",
                label=LocalizedText(**{"en-US": "Net revenue", "pt-BR": "Receita liquida"}),
            )
        ],
        state_patch_template={"metrics": "$selected_option"},
    )
    out_of_scope = OutOfScopeResponse(
        reason_code=OutOfScopeReasonCode.WRITE_OPERATION,
        message=LocalizedText(
            **{
                "en-US": "I cannot modify data or execute write operations.",
                "pt-BR": "Nao posso modificar dados nem executar operacoes de escrita.",
            }
        ),
    )

    assert render_text(response_from_clarification(clarification), "pt-BR").startswith("Qual")
    assert render_text(response_from_out_of_scope(out_of_scope), "en-US").startswith("I cannot")
