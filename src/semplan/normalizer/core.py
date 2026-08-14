"""Deterministic normalization from semantic requests to executable plans."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from pydantic import BaseModel, ConfigDict

from semplan.catalog.models import Catalog, MetricEntry
from semplan.contracts import (
    ClarificationOption,
    ClarificationQuestion,
    ClarificationReasonCode,
    DimensionSpec,
    ExecutionOperator,
    ExecutionPolicy,
    FilterSpec,
    Intent,
    LocalizedText,
    MetricSpec,
    Operation,
    Operator,
    OutOfScopeReasonCode,
    OutOfScopeResponse,
    PlanStatus,
    PredicateGroup,
    PredicateLeaf,
    ResultOutcome,
    SemanticPlanEnvelope,
    SemanticRequestEnvelope,
    SortSpec,
    TimeGrain,
)
from semplan.data_generation.writer import canonical_json
from semplan.errors import ErrorCode, ProjectError

NORMALIZER_VERSION = "0.1.0-f4"
GOVERNED_FILTER_OPERATORS = {
    "status": frozenset({Operator.EQ, Operator.IN, Operator.NOT_IN}),
    "end_date": frozenset(
        {
            Operator.EQ,
            Operator.GTE,
            Operator.LTE,
            Operator.BETWEEN,
            Operator.IS_NULL,
            Operator.IS_NOT_NULL,
        }
    ),
    "start_date": frozenset({Operator.EQ, Operator.GTE, Operator.LTE, Operator.BETWEEN}),
}
GOVERNED_FILTER_METRICS = {
    "status": frozenset({"active_contract_value"}),
    "end_date": frozenset({"active_contract_value"}),
    "start_date": frozenset({"active_contract_value"}),
}


class NormalizationResult(BaseModel):
    """Structured outcome of deterministic normalization."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: ResultOutcome
    plan: SemanticPlanEnvelope | None = None
    clarification: ClarificationQuestion | None = None
    out_of_scope: OutOfScopeResponse | None = None


@dataclass(frozen=True)
class ReferenceContext:
    reference_date: date
    timezone: str = "UTC"


def normalize_semantic_request(
    request: SemanticRequestEnvelope,
    catalog: Catalog,
    context: ReferenceContext,
    *,
    previous_plan: SemanticPlanEnvelope | None = None,
) -> NormalizationResult:
    """Normalize one strict semantic request without side effects."""

    if request.operation is Operation.CLARIFY:
        return NormalizationResult(
            outcome=ResultOutcome.CLARIFY,
            clarification=_build_clarification(request, catalog),
        )
    if request.operation is Operation.OUT_OF_SCOPE:
        return NormalizationResult(
            outcome=ResultOutcome.OUT_OF_SCOPE,
            out_of_scope=_build_out_of_scope(request.out_of_scope_reason),
        )

    previous = previous_plan if request.operation is Operation.PATCH else None
    if request.operation is Operation.PATCH and previous is None:
        raise ProjectError(
            ErrorCode.CFG_INVALID,
            "PATCH normalization requires previous structured state",
        )

    metric_ids, dimension_ids, filters, sort_specs, time_grain, limit, defaults = (
        _merge_request_state(
            request,
            previous,
            catalog,
        )
    )
    metrics = [_metric_spec(metric_id, catalog) for metric_id in metric_ids]
    dimensions = [DimensionSpec(id=dimension_id) for dimension_id in dimension_ids]

    _validate_dimensions(metric_ids, dimension_ids, catalog, requested=True)
    _validate_filters(filters, catalog)
    _validate_sort(sort_specs, metric_ids, dimension_ids)

    operator = _execution_operator(request.intent, dimensions, sort_specs, request.limit)
    predicate_tree = PredicateGroup(
        type="AND",
        children=[
            PredicateLeaf(
                type="predicate",
                field=filter_spec.field,
                operator=filter_spec.operator,
                value=filter_spec.value,
            )
            for filter_spec in filters
        ],
    )

    request_hash = _hash_payload(request.model_dump(mode="json"))
    catalog_hash = f"sha256:{catalog.sha256()}"
    payload_without_id = {
        "schema_version": "1.0",
        "operation": request.operation.value,
        "metric_specs": [metric.model_dump(mode="json") for metric in metrics],
        "dimension_specs": [dimension.model_dump(mode="json") for dimension in dimensions],
        "predicate_tree": predicate_tree.model_dump(mode="json"),
        "time_context": {
            "reference_date": context.reference_date.isoformat(),
            "timezone": context.timezone,
            "grain": time_grain.value if time_grain is not None else None,
        },
        "sort_specs": [sort.model_dump(mode="json") for sort in sort_specs],
        "limit": limit,
        "execution": {
            "operator": operator.value,
            "policy": ExecutionPolicy.READ_ONLY.value,
            "max_rows": 1000,
        },
        "provenance": {
            "request_hash": request_hash,
            "normalizer_version": NORMALIZER_VERSION,
            "catalog_hash": catalog_hash,
            "defaults": defaults,
        },
        "status": PlanStatus.READY.value,
    }
    plan_id = "uuid5:" + str(uuid.uuid5(uuid.NAMESPACE_URL, canonical_json(payload_without_id)))
    plan = SemanticPlanEnvelope.model_validate({"plan_id": plan_id, **payload_without_id})
    return NormalizationResult(outcome=ResultOutcome.ANSWERED, plan=plan)


def _merge_request_state(
    request: SemanticRequestEnvelope,
    previous: SemanticPlanEnvelope | None,
    catalog: Catalog,
) -> tuple[
    list[str],
    list[str],
    list[FilterSpec],
    list[SortSpec],
    TimeGrain | None,
    int | None,
    list[str],
]:
    defaults: list[str] = []
    if previous is None:
        metric_ids = list(request.metrics)
        dimension_ids = list(request.dimensions)
        filters = list(request.filters)
        sort_specs = list(request.sort)
        return (
            metric_ids,
            dimension_ids,
            filters,
            sort_specs,
            request.time_grain,
            request.limit,
            defaults,
        )

    metric_ids = [metric.id for metric in previous.metric_specs]
    dimension_ids = [dimension.id for dimension in previous.dimension_specs]
    filters = [
        FilterSpec(field=leaf.field, operator=leaf.operator, value=leaf.value)
        for leaf in _predicate_leaves(previous.predicate_tree)
    ]
    sort_specs = list(previous.sort_specs)
    time_grain = previous.time_context.grain
    limit = previous.limit

    if request.metrics:
        metric_ids = list(request.metrics)
    if request.dimensions:
        _validate_dimensions(metric_ids, list(request.dimensions), catalog, requested=True)
        dimension_ids = list(request.dimensions)
    if request.filters:
        _validate_filter_compatibility(list(request.filters), metric_ids, catalog, requested=True)
        patch_fields = {filter_spec.field for filter_spec in request.filters}
        filters = [filter_spec for filter_spec in filters if filter_spec.field not in patch_fields]
        filters.extend(request.filters)
    if request.sort:
        sort_specs = list(request.sort)
    if request.time_grain is not None:
        time_grain = request.time_grain
    if request.limit is not None:
        limit = request.limit

    compatible_dimensions = _compatible_dimensions(metric_ids, dimension_ids, catalog)
    removed = [
        dimension_id for dimension_id in dimension_ids if dimension_id not in compatible_dimensions
    ]
    if removed:
        defaults.extend(
            f"removed_incompatible_dimension:{dimension_id}" for dimension_id in removed
        )

    compatible_filters = _compatible_filters(filters, metric_ids, catalog)
    removed_filters = [
        filter_spec.field for filter_spec in filters if filter_spec not in compatible_filters
    ]
    if removed_filters:
        defaults.extend(f"removed_incompatible_filter:{field}" for field in removed_filters)

    if not request.sort:
        allowed_sort_fields = set(metric_ids).union(compatible_dimensions)
        removed_sorts = [sort.field for sort in sort_specs if sort.field not in allowed_sort_fields]
        if removed_sorts:
            defaults.extend(f"removed_incompatible_sort:{field}" for field in removed_sorts)
            sort_specs = [sort for sort in sort_specs if sort.field in allowed_sort_fields]
    return (
        metric_ids,
        compatible_dimensions,
        compatible_filters,
        sort_specs,
        time_grain,
        limit,
        defaults,
    )


def _predicate_leaves(
    predicate: PredicateGroup | PredicateLeaf,
) -> Iterable[PredicateLeaf]:
    if isinstance(predicate, PredicateLeaf):
        yield predicate
        return
    for child in predicate.children:
        yield from _predicate_leaves(child)


def _metric_spec(metric_id: str, catalog: Catalog) -> MetricSpec:
    metric = _metric(metric_id, catalog)
    return MetricSpec(id=metric.id, aggregation=metric.aggregation)


def _metric(metric_id: str, catalog: Catalog) -> MetricEntry:
    try:
        return catalog.metrics[metric_id]
    except KeyError as exc:
        raise ProjectError(
            ErrorCode.CATALOG_UNKNOWN_ID,
            "Unknown metric ID",
            detail={"metric_id": metric_id},
        ) from exc


def _validate_dimensions(
    metric_ids: list[str], dimension_ids: list[str], catalog: Catalog, *, requested: bool
) -> None:
    unknown = [
        dimension_id for dimension_id in dimension_ids if dimension_id not in catalog.dimensions
    ]
    if unknown:
        raise ProjectError(
            ErrorCode.CATALOG_UNKNOWN_ID,
            "Unknown dimension ID",
            detail={"dimension_ids": unknown},
        )
    incompatible = [
        dimension_id
        for dimension_id in dimension_ids
        if any(
            dimension_id not in _metric(metric_id, catalog).eligible_dimensions
            for metric_id in metric_ids
        )
    ]
    if incompatible and requested:
        raise ProjectError(
            ErrorCode.CATALOG_UNKNOWN_ID,
            "Dimension is not compatible with requested metrics",
            detail={"dimension_ids": incompatible, "metric_ids": metric_ids},
        )


def _compatible_dimensions(
    metric_ids: list[str], dimension_ids: list[str], catalog: Catalog
) -> list[str]:
    compatible: list[str] = []
    for dimension_id in dimension_ids:
        if dimension_id not in catalog.dimensions:
            raise ProjectError(
                ErrorCode.CATALOG_UNKNOWN_ID,
                "Unknown dimension ID",
                detail={"dimension_id": dimension_id},
            )
        if all(
            dimension_id in _metric(metric_id, catalog).eligible_dimensions
            for metric_id in metric_ids
        ):
            compatible.append(dimension_id)
    return compatible


def _validate_filters(filters: list[FilterSpec], catalog: Catalog) -> None:
    for filter_spec in filters:
        dimension = catalog.dimensions.get(filter_spec.field)
        if dimension is None:
            allowed_operators = GOVERNED_FILTER_OPERATORS.get(filter_spec.field)
            if allowed_operators is not None and filter_spec.operator in allowed_operators:
                continue
            raise ProjectError(
                ErrorCode.CATALOG_UNKNOWN_ID,
                "Filter references unknown field",
                detail={"field": filter_spec.field},
            )
        if filter_spec.operator not in dimension.allowed_operators:
            raise ProjectError(
                ErrorCode.CATALOG_UNKNOWN_ID,
                "Filter operator is not allowed for field",
                detail={"field": filter_spec.field, "operator": filter_spec.operator.value},
            )


def _validate_filter_compatibility(
    filters: list[FilterSpec], metric_ids: list[str], catalog: Catalog, *, requested: bool
) -> None:
    incompatible = [
        filter_spec.field
        for filter_spec in filters
        if _filter_incompatible(filter_spec.field, metric_ids, catalog)
    ]
    if incompatible and requested:
        raise ProjectError(
            ErrorCode.CATALOG_UNKNOWN_ID,
            "Filter is not compatible with requested metrics",
            detail={"fields": incompatible, "metric_ids": metric_ids},
        )


def _compatible_filters(
    filters: list[FilterSpec], metric_ids: list[str], catalog: Catalog
) -> list[FilterSpec]:
    compatible: list[FilterSpec] = []
    for filter_spec in filters:
        if filter_spec.field not in catalog.dimensions:
            if filter_spec.field not in GOVERNED_FILTER_OPERATORS:
                raise ProjectError(
                    ErrorCode.CATALOG_UNKNOWN_ID,
                    "Filter references unknown field",
                    detail={"field": filter_spec.field},
                )
        if not _filter_incompatible(filter_spec.field, metric_ids, catalog):
            compatible.append(filter_spec)
    return compatible


def _filter_incompatible(field: str, metric_ids: list[str], catalog: Catalog) -> bool:
    if field in catalog.dimensions:
        return any(
            field not in _metric(metric_id, catalog).eligible_dimensions for metric_id in metric_ids
        )
    allowed_metrics = GOVERNED_FILTER_METRICS.get(field)
    if allowed_metrics is None:
        return False
    return any(metric_id not in allowed_metrics for metric_id in metric_ids)


def _validate_sort(
    sort_specs: list[SortSpec], metric_ids: list[str], dimension_ids: list[str]
) -> None:
    allowed = set(metric_ids).union(dimension_ids)
    unknown = [sort.field for sort in sort_specs if sort.field not in allowed]
    if unknown:
        raise ProjectError(
            ErrorCode.CATALOG_UNKNOWN_ID,
            "Sort references unknown field for this plan",
            detail={"sort_fields": unknown, "allowed_fields": sorted(allowed)},
        )


def _execution_operator(
    intent: Intent, dimensions: list[DimensionSpec], sort_specs: list[SortSpec], limit: int | None
) -> ExecutionOperator:
    if intent is Intent.DETAIL_LOOKUP:
        return ExecutionOperator.DETAIL
    if intent is Intent.RANKING or (dimensions and sort_specs and limit is not None):
        return ExecutionOperator.RANK
    return ExecutionOperator.AGGREGATE


def _build_clarification(
    request: SemanticRequestEnvelope, catalog: Catalog
) -> ClarificationQuestion:
    clarification = request.clarifications[0]
    options = [
        ClarificationOption(
            option_id=option,
            label=_label_for_option(option, catalog),
        )
        for option in clarification.options
    ]
    payload = {
        "reason_code": clarification.reason_code.value,
        "question": clarification.question,
        "options": clarification.options,
    }
    return ClarificationQuestion(
        clarification_id="clar-"
        + hashlib.sha256(canonical_json(payload).encode()).hexdigest()[:16],
        reason_code=clarification.reason_code,
        question=_question_for_reason(clarification.reason_code),
        options=options,
        state_patch_template=_patch_template_for_reason(clarification.reason_code),
    )


def _label_for_option(option: str, catalog: Catalog) -> LocalizedText:
    if option in catalog.metrics:
        labels = catalog.metrics[option].labels
        return LocalizedText(
            **{
                "en-US": labels.en_us,
                "pt-BR": labels.pt_br,
            }
        )
    elif option in catalog.dimensions:
        labels = catalog.dimensions[option].labels
        return LocalizedText(
            **{
                "en-US": labels.en_us,
                "pt-BR": labels.pt_br,
            }
        )
    label = option.replace("_", " ")
    return LocalizedText(
        **{
            "en-US": label,
            "pt-BR": label,
        }
    )


def _question_for_reason(reason: ClarificationReasonCode) -> LocalizedText:
    questions = {
        ClarificationReasonCode.AMBIGUOUS_METRIC: {
            "en-US": "Which metric should be used?",
            "pt-BR": "Qual metrica deve ser usada?",
        },
        ClarificationReasonCode.AMBIGUOUS_DIMENSION: {
            "en-US": "Which dimension should be used?",
            "pt-BR": "Qual dimensao deve ser usada?",
        },
        ClarificationReasonCode.AMBIGUOUS_ENTITY: {
            "en-US": "Which entity did you mean?",
            "pt-BR": "Qual entidade voce quis dizer?",
        },
        ClarificationReasonCode.AMBIGUOUS_TIME_RANGE: {
            "en-US": "Which time range should be used?",
            "pt-BR": "Qual periodo deve ser usado?",
        },
        ClarificationReasonCode.AMBIGUOUS_COMPARISON_BASIS: {
            "en-US": "What should be used as the comparison basis?",
            "pt-BR": "Qual base de comparacao deve ser usada?",
        },
        ClarificationReasonCode.AMBIGUOUS_RANKING_DIRECTION: {
            "en-US": "Should the ranking be highest or lowest first?",
            "pt-BR": "A classificacao deve comecar pela maior ou pela menor?",
        },
        ClarificationReasonCode.AMBIGUOUS_SCOPE: {
            "en-US": "Which scope should be used?",
            "pt-BR": "Qual escopo deve ser usado?",
        },
    }
    return LocalizedText(**questions[reason])


def _patch_template_for_reason(reason: ClarificationReasonCode) -> dict[str, str]:
    if reason is ClarificationReasonCode.AMBIGUOUS_METRIC:
        return {"metrics": "$selected_option"}
    if reason is ClarificationReasonCode.AMBIGUOUS_DIMENSION:
        return {"dimensions": "$selected_option"}
    return {"filters": "$selected_option"}


def _build_out_of_scope(reason: OutOfScopeReasonCode | None) -> OutOfScopeResponse:
    if reason is None:
        reason = OutOfScopeReasonCode.MALFORMED_INPUT
    messages = {
        OutOfScopeReasonCode.UNSUPPORTED_DOMAIN: {
            "en-US": "I can only answer governed Northstar Commerce analytics questions.",
            "pt-BR": "So posso responder perguntas analiticas governadas da Northstar Commerce.",
        },
        OutOfScopeReasonCode.WRITE_OPERATION: {
            "en-US": "I cannot modify data or execute write operations.",
            "pt-BR": "Nao posso modificar dados nem executar operacoes de escrita.",
        },
        OutOfScopeReasonCode.PROHIBITED_DATA_ACCESS: {
            "en-US": "I cannot access prohibited or hidden data.",
            "pt-BR": "Nao posso acessar dados proibidos ou ocultos.",
        },
        OutOfScopeReasonCode.NON_ANALYTICS_REQUEST: {
            "en-US": "I can help with governed analytics questions only.",
            "pt-BR": "Posso ajudar apenas com perguntas analiticas governadas.",
        },
        OutOfScopeReasonCode.UNSUPPORTED_COMPUTATION: {
            "en-US": "That computation is outside the governed benchmark catalog.",
            "pt-BR": "Esse calculo esta fora do catalogo governado do benchmark.",
        },
        OutOfScopeReasonCode.PROMPT_EXTRACTION: {
            "en-US": "I cannot reveal system prompts or hidden benchmark metadata.",
            "pt-BR": "Nao posso revelar prompts de sistema nem metadados ocultos do benchmark.",
        },
        OutOfScopeReasonCode.MALFORMED_INPUT: {
            "en-US": "The request could not be interpreted safely.",
            "pt-BR": "A solicitacao nao pode ser interpretada com seguranca.",
        },
    }
    return OutOfScopeResponse(reason_code=reason, message=LocalizedText(**messages[reason]))


def _hash_payload(payload: object) -> str:
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"
