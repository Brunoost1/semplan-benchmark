"""Structured A4 session state and clarification resolution."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from semplan.catalog.models import Catalog
from semplan.contracts import (
    ClarificationQuestion,
    Intent,
    Operation,
    ResultOutcome,
    SemanticPlanEnvelope,
    SemanticRequestEnvelope,
)
from semplan.normalizer import NormalizationResult, ReferenceContext, normalize_semantic_request


@dataclass
class StructuredSession:
    """Authoritative structured state for A4 multi-turn workflows."""

    catalog: Catalog
    context: ReferenceContext
    previous_plan: SemanticPlanEnvelope | None = None
    pending_clarifications: list[ClarificationQuestion] = field(default_factory=list)

    def apply_request(self, request: SemanticRequestEnvelope) -> NormalizationResult:
        result = normalize_semantic_request(
            request,
            self.catalog,
            self.context,
            previous_plan=self.previous_plan,
        )
        if result.outcome is ResultOutcome.ANSWERED:
            self.previous_plan = result.plan
            self.pending_clarifications.clear()
        elif result.outcome is ResultOutcome.CLARIFY and result.clarification is not None:
            self.pending_clarifications = [result.clarification]
        elif result.outcome is ResultOutcome.OUT_OF_SCOPE:
            pass
        return result

    def answer_clarification(self, option_id: str) -> NormalizationResult:
        if not self.pending_clarifications:
            raise ValueError("No pending clarification exists")
        clarification = self.pending_clarifications[0]
        option_ids = {option.option_id for option in clarification.options}
        if option_id not in option_ids:
            raise ValueError(f"Unknown clarification option: {option_id}")
        patch = SemanticRequestEnvelope(
            schema_version="1.0",
            operation=Operation.PATCH,
            intent=Intent.GROUPED_METRIC,
            metrics=[option_id] if "metrics" in clarification.state_patch_template else [],
            dimensions=[option_id] if "dimensions" in clarification.state_patch_template else [],
            filters=[],
            time_grain=None,
            sort=[],
            limit=None,
            comparison=None,
            clarifications=[],
            confidence=Decimal("1"),
        )
        result = normalize_semantic_request(
            patch,
            self.catalog,
            self.context,
            previous_plan=self.previous_plan,
        )
        if result.plan is not None:
            self.previous_plan = result.plan
        self.pending_clarifications.clear()
        return result
