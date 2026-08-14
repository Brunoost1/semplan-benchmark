"""Deterministic canonical and human-readable response rendering."""

from __future__ import annotations

from semplan.contracts import (
    CanonicalResponse,
    ClarificationQuestion,
    LocalizedText,
    OutOfScopeResponse,
    ResultOutcome,
    ScalarValue,
)
from semplan.executor.semantic import SemanticExecutionResult


def response_from_execution(
    execution: SemanticExecutionResult,
    *,
    assumptions: list[str] | None = None,
) -> CanonicalResponse:
    return CanonicalResponse(
        schema_version="1.0",
        outcome=ResultOutcome.ANSWERED,
        rows=execution.rows,
        units=execution.units,
        assumptions=assumptions or [],
        message=_answer_message(execution.rows),
    )


def response_from_clarification(clarification: ClarificationQuestion) -> CanonicalResponse:
    return CanonicalResponse(
        schema_version="1.0",
        outcome=ResultOutcome.CLARIFY,
        clarification=clarification,
        message=clarification.question,
    )


def response_from_out_of_scope(out_of_scope: OutOfScopeResponse) -> CanonicalResponse:
    return CanonicalResponse(
        schema_version="1.0",
        outcome=ResultOutcome.OUT_OF_SCOPE,
        out_of_scope=out_of_scope,
        message=out_of_scope.message,
    )


def render_text(response: CanonicalResponse, locale: str) -> str:
    """Render deterministic display text without changing canonical values."""

    if response.message is not None:
        return _localized(response.message, locale)
    if response.outcome is ResultOutcome.ANSWERED:
        return _localized(_answer_message(response.rows), locale)
    return _localized(
        LocalizedText(
            **{
                "en-US": "No answer is available.",
                "pt-BR": "Nenhuma resposta esta disponivel.",
            }
        ),
        locale,
    )


def _answer_message(rows: list[dict[str, ScalarValue]]) -> LocalizedText:
    if not rows:
        return LocalizedText(
            **{
                "en-US": "The governed query returned no rows.",
                "pt-BR": "A consulta governada nao retornou linhas.",
            }
        )
    return LocalizedText(
        **{
            "en-US": f"Returned {len(rows)} canonical row(s).",
            "pt-BR": f"Retornou {len(rows)} linha(s) canonica(s).",
        }
    )


def _localized(text: LocalizedText, locale: str) -> str:
    if locale == "pt-BR":
        return text.pt_br
    return text.en_us
