"""A3/A4 semantic-plan approach orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from semplan.catalog.models import Catalog
from semplan.contracts import (
    Approach,
    BenchmarkCase,
    CanonicalResponse,
    ProviderFinishStatus,
    ProviderResponse,
    ResultOutcome,
    SemanticPlanEnvelope,
    SemanticRequestEnvelope,
)
from semplan.errors import ErrorCode, ProjectError
from semplan.executor import SemanticExecutionResult, execute_semantic_plan
from semplan.normalizer import ReferenceContext, normalize_semantic_request
from semplan.prompts import PromptRegistry
from semplan.providers import ModelProvider, build_provider_request
from semplan.reporting import (
    response_from_clarification,
    response_from_execution,
    response_from_out_of_scope,
)
from semplan.sessions import StructuredSession


@dataclass(frozen=True)
class SemanticPlanRunResult:
    approach: Approach
    case_id: str
    outcome: ResultOutcome
    provider_response: ProviderResponse
    semantic_request: SemanticRequestEnvelope
    plan: SemanticPlanEnvelope | None
    execution: SemanticExecutionResult | None
    response: CanonicalResponse


class SemanticPlanRunner:
    """Run A3 or A4 through provider, normalizer, executor, and renderer."""

    def __init__(
        self,
        *,
        approach: Approach,
        provider: ModelProvider,
        catalog: Catalog,
        prompt_registry: PromptRegistry,
        database_url: str | None = None,
        provider_name: str = "fake",
        model_name: str = "fake-semantic-request-v1",
        inference_parameters: dict[str, object] | None = None,
        request_metadata: dict[str, str] | None = None,
    ) -> None:
        if approach not in {Approach.A3, Approach.A4}:
            raise ValueError("SemanticPlanRunner supports A3 and A4 only")
        self.approach = approach
        self.provider = provider
        self.catalog = catalog
        self.prompt = prompt_registry.for_approach(approach)
        self.database_url = database_url
        self.provider_name = provider_name
        self.model_name = model_name
        self.inference_parameters = dict(inference_parameters or {"temperature": "0"})
        self.request_metadata = dict(request_metadata or {})
        self.session: StructuredSession | None = None

    def run_case(self, case: BenchmarkCase) -> SemanticPlanRunResult:
        if self.approach is Approach.A4 and self.session is None:
            self.session = StructuredSession(
                self.catalog,
                ReferenceContext(case.context.reference_date, case.context.timezone),
            )
        context = ReferenceContext(case.context.reference_date, case.context.timezone)
        prompt_text = self.prompt.render(
            {
                "locale": case.language.value,
                "reference_date": case.context.reference_date.isoformat(),
                "utterance": case.utterance,
                "catalog_summary": _catalog_summary(self.catalog),
                "previous_state": _previous_state_json(self.session)
                if self.approach is Approach.A4
                else "{}",
                "pending_clarifications": _pending_clarifications_json(self.session)
                if self.approach is Approach.A4
                else "[]",
            }
        )
        provider_request = build_provider_request(
            provider=self.provider_name,
            model=self.model_name,
            prompt_id=self.prompt.metadata.prompt_id,
            prompt_sha256=self.prompt.sha256,
            system=prompt_text,
            inputs=[case.utterance],
            output_schema_ref=self.prompt.metadata.expected_output_schema,
            output_schema_sha256=self.prompt.output_schema_sha256,
            inference_parameters=self.inference_parameters,
            timeout_seconds=30,
            metadata={
                "approach": self.approach.value,
                "case_id": case.case_id,
                "prompt_sha256": self.prompt.sha256,
                **self.request_metadata,
            },
        )
        provider_response = self.provider.complete(provider_request)
        if provider_response.finish_status is not ProviderFinishStatus.STOP:
            raise ProjectError(
                ErrorCode.OUTPUT_SCHEMA_INVALID,
                "Provider did not return a completed semantic request",
                detail={"finish_status": provider_response.finish_status.value},
            )
        try:
            semantic_request = SemanticRequestEnvelope.model_validate(
                provider_response.parsed_payload
            )
        except ValidationError as exc:
            raise ProjectError(
                ErrorCode.OUTPUT_SCHEMA_INVALID,
                "Provider semantic-request payload failed schema validation",
                detail={"schema": "SemanticRequestEnvelope", "error_count": len(exc.errors())},
            ) from exc

        try:
            if self.approach is Approach.A4:
                assert self.session is not None
                normalized = self.session.apply_request(semantic_request)
            else:
                normalized = normalize_semantic_request(semantic_request, self.catalog, context)
        except ValidationError as exc:
            raise ProjectError(
                ErrorCode.OUTPUT_SCHEMA_INVALID,
                "Semantic request normalization failed contract validation",
                detail={"schema": "SemanticRequestEnvelope", "error_count": len(exc.errors())},
            ) from exc

        execution = None
        if normalized.outcome is ResultOutcome.ANSWERED:
            if normalized.plan is None:
                raise ProjectError(ErrorCode.CFG_INVALID, "ANSWERED outcome is missing plan")
            execution = execute_semantic_plan(
                normalized.plan,
                self.catalog,
                database_url=self.database_url,
            )
            response = response_from_execution(execution)
        elif normalized.outcome is ResultOutcome.CLARIFY and normalized.clarification is not None:
            response = response_from_clarification(normalized.clarification)
        elif (
            normalized.outcome is ResultOutcome.OUT_OF_SCOPE and normalized.out_of_scope is not None
        ):
            response = response_from_out_of_scope(normalized.out_of_scope)
        else:
            raise ProjectError(ErrorCode.CFG_INVALID, "Invalid normalization outcome")

        return SemanticPlanRunResult(
            approach=self.approach,
            case_id=case.case_id,
            outcome=normalized.outcome,
            provider_response=provider_response,
            semantic_request=semantic_request,
            plan=normalized.plan,
            execution=execution,
            response=response,
        )


def _catalog_summary(catalog: Catalog) -> str:
    metrics = ", ".join(sorted(catalog.metrics))
    dimensions = ", ".join(sorted(catalog.dimensions))
    return f"metrics: {metrics}\ndimensions: {dimensions}"


def _previous_state_json(session: StructuredSession | None) -> str:
    if session is None or session.previous_plan is None:
        return "{}"
    return session.previous_plan.model_dump_json()


def _pending_clarifications_json(session: StructuredSession | None) -> str:
    if session is None:
        return "[]"
    return "[" + ",".join(item.model_dump_json() for item in session.pending_clarifications) + "]"


def default_prompt_registry(root: Path | None = None) -> PromptRegistry:
    return PromptRegistry.load(root or Path("prompts"))
