"""Strict, versioned public contracts for benchmark artifacts."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "1.0"
BENCHMARK_CASE_ID_PATTERN = r"^(DEV|VAL|TST-PUB|TST-HID|MT|ADV)-(SMK|REL)-[0-9]{6}$"
CanonicalId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]
IdentifierList = list[CanonicalId]
ScalarValue = str | int | Decimal | date | bool | None
FilterValue = ScalarValue | list[ScalarValue]


class StrictContractModel(BaseModel):
    """Base for repository contracts: strict, immutable, and schema-friendly."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Locale(StrEnum):
    EN_US = "en-US"
    PT_BR = "pt-BR"


class Approach(StrEnum):
    A1 = "A1"
    A2 = "A2"
    A3 = "A3"
    A4 = "A4"


class Operation(StrEnum):
    REPLACE = "REPLACE"
    PATCH = "PATCH"
    CLARIFY = "CLARIFY"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


class Intent(StrEnum):
    GROUPED_METRIC = "grouped_metric"
    DETAIL_LOOKUP = "detail_lookup"
    RANKING = "ranking"
    COMPARISON = "comparison"
    CLARIFICATION = "clarification"
    OUT_OF_SCOPE = "out_of_scope"


class Operator(StrEnum):
    EQ = "EQ"
    NE = "NE"
    IN = "IN"
    NOT_IN = "NOT_IN"
    GT = "GT"
    GTE = "GTE"
    LT = "LT"
    LTE = "LTE"
    BETWEEN = "BETWEEN"
    IS_NULL = "IS_NULL"
    IS_NOT_NULL = "IS_NOT_NULL"
    CONTAINS_CANONICAL = "CONTAINS_CANONICAL"


class Direction(StrEnum):
    ASC = "asc"
    DESC = "desc"


class TimeGrain(StrEnum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"


class Aggregation(StrEnum):
    SUM = "SUM"
    COUNT_DISTINCT = "COUNT_DISTINCT"
    RATIO = "RATIO"
    DERIVED = "DERIVED"


class PlanStatus(StrEnum):
    READY = "READY"
    CLARIFY = "CLARIFY"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    ERROR = "ERROR"


class ExecutionOperator(StrEnum):
    AGGREGATE = "aggregate"
    DETAIL = "detail"
    RANK = "rank"


class ExecutionPolicy(StrEnum):
    READ_ONLY = "read_only"


class ResultOutcome(StrEnum):
    ANSWERED = "ANSWERED"
    CLARIFY = "CLARIFY"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    ERROR = "ERROR"


class ClarificationReasonCode(StrEnum):
    AMBIGUOUS_METRIC = "AMBIGUOUS_METRIC"
    AMBIGUOUS_DIMENSION = "AMBIGUOUS_DIMENSION"
    AMBIGUOUS_ENTITY = "AMBIGUOUS_ENTITY"
    AMBIGUOUS_TIME_RANGE = "AMBIGUOUS_TIME_RANGE"
    AMBIGUOUS_COMPARISON_BASIS = "AMBIGUOUS_COMPARISON_BASIS"
    AMBIGUOUS_RANKING_DIRECTION = "AMBIGUOUS_RANKING_DIRECTION"
    AMBIGUOUS_SCOPE = "AMBIGUOUS_SCOPE"


class OutOfScopeReasonCode(StrEnum):
    UNSUPPORTED_DOMAIN = "UNSUPPORTED_DOMAIN"
    WRITE_OPERATION = "WRITE_OPERATION"
    PROHIBITED_DATA_ACCESS = "PROHIBITED_DATA_ACCESS"
    NON_ANALYTICS_REQUEST = "NON_ANALYTICS_REQUEST"
    UNSUPPORTED_COMPUTATION = "UNSUPPORTED_COMPUTATION"
    PROMPT_EXTRACTION = "PROMPT_EXTRACTION"
    MALFORMED_INPUT = "MALFORMED_INPUT"


class ProviderFinishStatus(StrEnum):
    STOP = "STOP"
    REFUSAL = "REFUSAL"
    INCOMPLETE = "INCOMPLETE"
    ERROR = "ERROR"


class ProviderHealthStatus(StrEnum):
    OK = "OK"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"


class ToolValidationOutcome(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class CacheEntryState(StrEnum):
    RESERVED = "reserved"
    IN_FLIGHT = "in_flight"
    COMPLETED = "completed"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_TERMINAL = "failed_terminal"


class RunManifestStatus(StrEnum):
    DRAFT = "draft"
    VALIDATED = "validated"
    FROZEN = "frozen"
    RUNNING = "running"
    COMPLETED = "completed"
    ABORTED = "aborted"
    INVALIDATED = "invalidated"


class ExperimentMode(StrEnum):
    DRY_RUN = "dry-run"
    REPLAY = "replay"
    PILOT = "pilot"
    SYNCHRONOUS = "synchronous"
    BATCH = "batch"


class WorkItemStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_TERMINAL = "failed_terminal"
    SKIPPED = "skipped"


class AnalysisRole(StrEnum):
    PRIMARY = "primary"
    STABILITY = "stability"


class PredictionStatus(StrEnum):
    PARSED = "parsed"
    INVALID = "invalid"
    REFUSED = "refused"
    INCOMPLETE = "incomplete"
    ERROR = "error"


class DatasetSplit(StrEnum):
    DEVELOPMENT = "development"
    VALIDATION = "validation"
    TEST_PUBLIC = "test_public"
    TEST_HIDDEN = "test_hidden"
    MULTI_TURN = "multi_turn"
    ADVERSARIAL = "adversarial"


class Difficulty(StrEnum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class ExpectedPolicy(StrEnum):
    ALLOW = "ALLOW"
    CLARIFY = "CLARIFY"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    POLICY_VIOLATION = "POLICY_VIOLATION"


class ReviewStatus(StrEnum):
    PENDING_AUTHOR_REVIEW = "pending_author_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class QuestionClass(StrEnum):
    LOOKUP = "lookup"
    GROUPED_AGGREGATION = "grouped_aggregation"
    RANKING = "ranking"
    COMPARISON = "comparison"
    VARIANCE = "variance"
    TREND = "trend"
    SHARE_RATIO = "share_ratio"
    FILTERING = "filtering"
    CONTRACT_STATUS = "contract_status"
    AMBIGUITY = "ambiguity"
    OUT_OF_SCOPE = "out_of_scope"
    MULTI_TURN = "multi_turn"
    ADVERSARIAL = "adversarial"


class FilterSpec(StrictContractModel):
    field: CanonicalId
    operator: Operator
    value: FilterValue = None

    @model_validator(mode="after")
    def validate_operator_value(self) -> FilterSpec:
        if self.operator in {Operator.IS_NULL, Operator.IS_NOT_NULL}:
            if self.value is not None:
                raise ValueError(f"{self.operator} filters must use null value")
            return self
        if self.operator is Operator.BETWEEN:
            if not isinstance(self.value, list) or len(self.value) != 2:
                raise ValueError("BETWEEN filters require exactly two values")
            return self
        if self.operator in {Operator.IN, Operator.NOT_IN}:
            if not isinstance(self.value, list) or len(self.value) == 0:
                raise ValueError(f"{self.operator} filters require a non-empty value list")
            return self
        if isinstance(self.value, list):
            raise ValueError(f"{self.operator} filters require a scalar value")
        return self


class SortSpec(StrictContractModel):
    field: CanonicalId
    direction: Direction


class ComparisonSpec(StrictContractModel):
    mode: Literal["period_over_period", "share_of_total"]
    baseline: str | None = None


class ClarificationSpec(StrictContractModel):
    reason_code: ClarificationReasonCode
    question: str = Field(min_length=1)
    options: list[str] = Field(min_length=1)


class SemanticRequestEnvelope(StrictContractModel):
    schema_version: Literal["1.0"]
    operation: Operation
    intent: Intent
    metrics: IdentifierList
    dimensions: IdentifierList
    filters: list[FilterSpec]
    time_grain: TimeGrain | None = None
    sort: list[SortSpec]
    limit: int | None = Field(default=None, ge=1, le=1000)
    comparison: ComparisonSpec | None = None
    clarifications: list[ClarificationSpec]
    out_of_scope_reason: OutOfScopeReasonCode | None = None
    confidence: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))

    @model_validator(mode="after")
    def validate_operation_shape(self) -> SemanticRequestEnvelope:
        if self.operation is Operation.CLARIFY and not self.clarifications:
            raise ValueError("CLARIFY requests must contain at least one clarification")
        if self.operation is Operation.OUT_OF_SCOPE and self.intent is not Intent.OUT_OF_SCOPE:
            raise ValueError("OUT_OF_SCOPE operation must use out_of_scope intent")
        if self.operation is Operation.OUT_OF_SCOPE and self.out_of_scope_reason is None:
            raise ValueError("OUT_OF_SCOPE requests must include out_of_scope_reason")
        if self.operation is not Operation.OUT_OF_SCOPE and self.out_of_scope_reason is not None:
            raise ValueError("Only OUT_OF_SCOPE requests may include out_of_scope_reason")
        if self.operation is Operation.REPLACE and not self.metrics:
            raise ValueError("REPLACE requests must include at least one metric")
        if self.operation is Operation.PATCH and not any(
            [
                self.metrics,
                self.dimensions,
                self.filters,
                self.time_grain is not None,
                self.sort,
                self.limit is not None,
                self.comparison is not None,
            ]
        ):
            raise ValueError("PATCH requests must include at least one analytical change")
        return self


class MetricSpec(StrictContractModel):
    id: CanonicalId
    aggregation: Aggregation


class DimensionSpec(StrictContractModel):
    id: CanonicalId


class PredicateLeaf(StrictContractModel):
    type: Literal["predicate"]
    field: CanonicalId
    operator: Operator
    value: FilterValue = None


class PredicateGroup(StrictContractModel):
    type: Literal["AND", "OR"]
    children: list[PredicateLeaf | PredicateGroup] = Field(default_factory=list)


class TimeContext(StrictContractModel):
    reference_date: date
    timezone: str = Field(min_length=1)
    grain: TimeGrain | None = None


class ExecutionSpec(StrictContractModel):
    operator: ExecutionOperator
    policy: ExecutionPolicy
    max_rows: int = Field(gt=0, le=10000)


class ProvenanceSpec(StrictContractModel):
    request_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    normalizer_version: str = Field(min_length=1)
    catalog_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    defaults: list[str] = Field(default_factory=list)


class SemanticPlanEnvelope(StrictContractModel):
    schema_version: Literal["1.0"]
    plan_id: str = Field(min_length=1)
    operation: Operation
    metric_specs: list[MetricSpec] = Field(default_factory=list)
    dimension_specs: list[DimensionSpec] = Field(default_factory=list)
    predicate_tree: PredicateGroup | PredicateLeaf
    time_context: TimeContext
    sort_specs: list[SortSpec] = Field(default_factory=list)
    limit: int | None = Field(default=None, ge=1, le=1000)
    execution: ExecutionSpec
    provenance: ProvenanceSpec
    status: PlanStatus = PlanStatus.READY


class BenchmarkContext(StrictContractModel):
    reference_date: date
    timezone: str = Field(min_length=1)


class BenchmarkReview(StrictContractModel):
    status: ReviewStatus
    reviewer: str | None = None
    reviewed_at: datetime | None = None
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_approval_metadata(self) -> BenchmarkReview:
        if self.status is ReviewStatus.APPROVED and (
            self.reviewer is None or self.reviewed_at is None
        ):
            raise ValueError("approved review requires reviewer and reviewed_at")
        return self


class ClarificationTarget(StrictContractModel):
    question_intent: str = Field(min_length=1)
    acceptable_resolution_choices: list[str] = Field(min_length=1)


class LocalizedText(StrictContractModel):
    en_us: str = Field(alias="en-US", min_length=1)
    pt_br: str = Field(alias="pt-BR", min_length=1)


class ClarificationOption(StrictContractModel):
    option_id: CanonicalId
    label: LocalizedText


class ClarificationQuestion(StrictContractModel):
    clarification_id: str = Field(pattern=r"^clar-[0-9a-f]{16}$")
    reason_code: ClarificationReasonCode
    question: LocalizedText
    options: list[ClarificationOption] = Field(min_length=1)
    state_patch_template: dict[str, str]


class OutOfScopeResponse(StrictContractModel):
    reason_code: OutOfScopeReasonCode
    message: LocalizedText


class BenchmarkCase(StrictContractModel):
    schema_version: Literal["1.0"]
    case_id: str = Field(pattern=BENCHMARK_CASE_ID_PATTERN)
    split: DatasetSplit
    language: Locale
    utterance: str = Field(min_length=1)
    context: BenchmarkContext
    expected_operation: Operation
    intent: QuestionClass
    difficulty: Difficulty
    requires_clarification: bool
    gold_semantic_plan_ref: str | None = None
    gold_sql_ref: str | None = None
    gold_answer_ref: str
    expected_policy: ExpectedPolicy
    tags: list[CanonicalId] = Field(min_length=1)
    template_family: CanonicalId
    semantic_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    clarification: ClarificationTarget | None = None
    review: BenchmarkReview

    @model_validator(mode="after")
    def validate_gold_refs_and_clarification(self) -> BenchmarkCase:
        if self.expected_policy is ExpectedPolicy.ALLOW and (
            self.gold_semantic_plan_ref is None or self.gold_sql_ref is None
        ):
            raise ValueError("ALLOW cases require semantic plan and SQL references")
        if self.requires_clarification and self.clarification is None:
            raise ValueError("clarification cases require clarification target metadata")
        if self.expected_policy is not ExpectedPolicy.CLARIFY and self.requires_clarification:
            raise ValueError("requires_clarification cases must use CLARIFY policy")
        return self


class OrderingSpec(StrictContractModel):
    ordered: bool
    fields: list[CanonicalId] = Field(default_factory=list)
    tie_policy: str = Field(min_length=1)


class ToleranceSpec(StrictContractModel):
    absolute: Decimal = Field(ge=Decimal("0"))
    relative: Decimal = Field(ge=Decimal("0"))


class GoldAnswer(StrictContractModel):
    schema_version: Literal["1.0"]
    case_id: str = Field(pattern=BENCHMARK_CASE_ID_PATTERN)
    outcome: ExpectedPolicy
    dataset_version: str = Field(min_length=1)
    dataset_manifest_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    query_hash: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    plan_hash: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    execution_timestamp_utc: datetime
    rows: list[dict[str, ScalarValue]]
    units: dict[CanonicalId, str]
    ordering: OrderingSpec
    tolerances: dict[CanonicalId, ToleranceSpec]
    assumptions: list[str] = Field(default_factory=list)
    review: BenchmarkReview


class BenchmarkManifest(StrictContractModel):
    schema_version: Literal["1.0"]
    benchmark_version: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    dataset_manifest_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    state: Literal["draft", "validated", "frozen", "evaluated", "released", "deprecated"]
    case_count: int = Field(ge=0)
    split_counts: dict[DatasetSplit, int]
    language_counts: dict[Locale, int]
    file_hashes: dict[str, str]
    hidden_included: bool
    review_summary: dict[ReviewStatus, int]


class ExperimentModelConfig(StrictContractModel):
    provider: str = Field(min_length=1)
    id: str = Field(min_length=1)
    reasoning_effort: str = Field(min_length=1)
    parameters: dict[str, ScalarValue] = Field(default_factory=dict)


class PromptBinding(StrictContractModel):
    id: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    output_schema_ref: str = Field(min_length=1)
    output_schema_sha256: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")


class ExecutionDesign(StrictContractModel):
    schema_version: Literal["1.0"]
    design_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]+$")
    analysis_plan: str = Field(min_length=1)
    scientific_case_ids: list[str] = Field(min_length=1)
    primary_repetitions: int = Field(ge=1, le=100)
    stability_subset_case_ids: list[str] = Field(default_factory=list)
    stability_additional_repetitions: int = Field(default=0, ge=0, le=100)
    stability_sampling_seed: int | None = None
    stability_sampling_algorithm: str | None = Field(default=None, min_length=1)
    stability_subset_sha256: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_execution_design(self) -> ExecutionDesign:
        if len(set(self.scientific_case_ids)) != len(self.scientific_case_ids):
            raise ValueError("scientific_case_ids must be unique")
        if len(set(self.stability_subset_case_ids)) != len(self.stability_subset_case_ids):
            raise ValueError("stability_subset_case_ids must be unique")
        if not set(self.stability_subset_case_ids).issubset(self.scientific_case_ids):
            raise ValueError("stability subset must be drawn from scientific_case_ids")
        if self.stability_additional_repetitions > 0:
            if not self.stability_subset_case_ids:
                raise ValueError("stability repetitions require stability_subset_case_ids")
            if self.stability_sampling_seed is None:
                raise ValueError("stability repetitions require stability_sampling_seed")
            if self.stability_sampling_algorithm is None:
                raise ValueError("stability repetitions require stability_sampling_algorithm")
            if self.stability_subset_sha256 is None:
                raise ValueError("stability repetitions require stability_subset_sha256")
        return self

    @property
    def max_repetitions(self) -> int:
        return self.primary_repetitions + self.stability_additional_repetitions


class RunManifest(StrictContractModel):
    schema_version: Literal["1.0"]
    run_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]+$")
    status: RunManifestStatus
    created_at_utc: datetime
    code_commit: str = Field(pattern=r"^([0-9a-f]{7,40}|unknown)$")
    dirty_tree: bool
    non_reportable: bool = False
    benchmark_version: str | None = Field(default=None, min_length=1)
    dataset_version: str = Field(min_length=1)
    dataset_manifest_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    benchmark_manifest_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    catalog_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    approaches: list[Approach] = Field(min_length=1)
    model: ExperimentModelConfig
    prompts: dict[Approach, PromptBinding] = Field(min_length=1)
    splits: list[DatasetSplit] = Field(min_length=1)
    repetitions: int = Field(ge=1, le=100)
    execution_design: ExecutionDesign | None = None
    randomization_seed: int
    budget_usd: Decimal = Field(ge=Decimal("0"))
    price_table_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    execution_policy_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    mode: ExperimentMode
    allow_paid: bool
    environment: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_scientific_bindings(self) -> RunManifest:
        missing_prompts = sorted(
            approach.value for approach in self.approaches if approach not in self.prompts
        )
        if missing_prompts:
            raise ValueError(
                f"Missing prompt bindings for approaches: {', '.join(missing_prompts)}"
            )
        if DatasetSplit.TEST_HIDDEN in self.splits and self.status is not RunManifestStatus.FROZEN:
            raise ValueError("test_hidden may only appear in frozen manifests")
        if (
            self.execution_design is not None
            and self.repetitions != self.execution_design.max_repetitions
        ):
            raise ValueError("repetitions must equal execution_design max repetitions")
        return self


class ArtifactRef(StrictContractModel):
    path: str = Field(pattern=r"^[A-Za-z0-9_./-]+$")
    sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class ResultInputRef(StrictContractModel):
    utterance_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    state_sha256: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    split: DatasetSplit
    language: Locale


class ResultProviderRef(StrictContractModel):
    request_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    request_ref: ArtifactRef
    response_ref: ArtifactRef
    model_requested: str = Field(min_length=1)
    model_returned: str = Field(min_length=1)
    usage: ProviderUsage
    cost_usd: Decimal = Field(ge=Decimal("0"))


class ResultPredictionRef(StrictContractModel):
    status: PredictionStatus
    artifact_ref: ArtifactRef | None = None


class ResultExecutionRef(StrictContractModel):
    policy: ExpectedPolicy
    executed_database: bool
    query_sha256: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    duration_ms: int = Field(ge=0)
    row_count: int = Field(ge=0)
    result_ref: ArtifactRef | None = None


class ScoreSummary(StrictContractModel):
    answer_correct: bool | None
    unsafe_or_invalid: bool
    semantic_exact: bool | None
    semantic_component_precision: Decimal | None = Field(
        default=None, ge=Decimal("0"), le=Decimal("1")
    )
    semantic_component_recall: Decimal | None = Field(
        default=None, ge=Decimal("0"), le=Decimal("1")
    )
    semantic_component_f1: Decimal | None = Field(default=None, ge=Decimal("0"), le=Decimal("1"))
    execution_success: bool
    clarification_decision_correct: bool | None
    sequence_state_correct: bool | None
    policy_correct: bool
    false_refusal: bool
    cost_usd: Decimal = Field(ge=Decimal("0"))
    latency_ms: int = Field(ge=0)
    provider_latency_ms: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class ResultError(StrictContractModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    retryable: bool
    layer: str = Field(min_length=1)


class ResultRecord(StrictContractModel):
    schema_version: Literal["1.0"]
    run_id: str = Field(min_length=1)
    work_item_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    case_id: str = Field(min_length=1)
    approach: Approach
    repetition: int = Field(ge=1)
    analysis_role: AnalysisRole = AnalysisRole.PRIMARY
    input: ResultInputRef
    provider: ResultProviderRef | None
    prediction: ResultPredictionRef
    execution: ResultExecutionRef
    scores: ScoreSummary
    score_ref: ArtifactRef
    errors: list[ResultError] = Field(default_factory=list)
    timestamps: dict[str, datetime] = Field(min_length=1)
    outcome: ResultOutcome


class ToolCallEnvelope(StrictContractModel):
    """Closed A2 tool-call contract scaffold for server-side validation."""

    schema_version: Literal["1.0"]
    tool_name: CanonicalId
    arguments: dict[str, ScalarValue]

    @field_validator("arguments")
    @classmethod
    def reject_empty_argument_names(cls, value: dict[str, ScalarValue]) -> dict[str, ScalarValue]:
        for key in value:
            if not key or not key.replace("_", "").isalnum():
                raise ValueError("Tool argument names must be simple identifiers")
        return value


class DirectSqlEnvelope(StrictContractModel):
    schema_version: Literal["1.0"]
    sql: str | None = Field(default=None, min_length=1)
    assumptions: list[str] = Field(default_factory=list)
    cannot_answer: bool
    reason_code: OutOfScopeReasonCode | None = None

    @model_validator(mode="after")
    def validate_answer_shape(self) -> DirectSqlEnvelope:
        if self.cannot_answer:
            if self.sql is not None:
                raise ValueError("cannot_answer responses must not include sql")
            if self.reason_code is None:
                raise ValueError("cannot_answer responses require reason_code")
        else:
            if self.sql is None:
                raise ValueError("answerable direct-SQL responses require sql")
            if self.reason_code is not None:
                raise ValueError("reason_code is only valid when cannot_answer is true")
        return self


class AggregateToolArguments(StrictContractModel):
    metrics: IdentifierList = Field(min_length=1)
    dimensions: IdentifierList = Field(default_factory=list)
    filters: list[FilterSpec] = Field(default_factory=list)
    time_grain: TimeGrain | None = None
    sort: list[SortSpec] = Field(default_factory=list)
    limit: int | None = Field(default=None, ge=1, le=1000)


class RankToolArguments(AggregateToolArguments):
    sort: list[SortSpec] = Field(min_length=1)


class ComparePeriodsToolArguments(StrictContractModel):
    metric: CanonicalId
    dimensions: IdentifierList = Field(default_factory=list)
    filters: list[FilterSpec] = Field(default_factory=list)
    time_grain: TimeGrain = TimeGrain.MONTH
    limit: int | None = Field(default=None, ge=1, le=1000)


class CompareActualBudgetToolArguments(StrictContractModel):
    dimensions: IdentifierList = Field(default_factory=lambda: ["expense_category"])
    filters: list[FilterSpec] = Field(default_factory=list)
    limit: int | None = Field(default=None, ge=1, le=1000)


class ContractStatusToolArguments(StrictContractModel):
    dimensions: IdentifierList = Field(default_factory=lambda: ["contract_risk"])
    filters: list[FilterSpec] = Field(default_factory=list)
    limit: int | None = Field(default=None, ge=1, le=1000)


class DescribeSupportedFieldsToolArguments(StrictContractModel):
    include_metrics: bool = True
    include_dimensions: bool = True


class AggregateToolCall(StrictContractModel):
    schema_version: Literal["1.0"]
    tool_name: Literal["aggregate"]
    arguments: AggregateToolArguments


class RankToolCall(StrictContractModel):
    schema_version: Literal["1.0"]
    tool_name: Literal["rank"]
    arguments: RankToolArguments


class ComparePeriodsToolCall(StrictContractModel):
    schema_version: Literal["1.0"]
    tool_name: Literal["compare_periods"]
    arguments: ComparePeriodsToolArguments


class CompareActualBudgetToolCall(StrictContractModel):
    schema_version: Literal["1.0"]
    tool_name: Literal["compare_actual_budget"]
    arguments: CompareActualBudgetToolArguments


class ContractStatusToolCall(StrictContractModel):
    schema_version: Literal["1.0"]
    tool_name: Literal["contract_status"]
    arguments: ContractStatusToolArguments


class DescribeSupportedFieldsToolCall(StrictContractModel):
    schema_version: Literal["1.0"]
    tool_name: Literal["describe_supported_fields"]
    arguments: DescribeSupportedFieldsToolArguments


ToolAgentToolCall = Annotated[
    AggregateToolCall
    | RankToolCall
    | ComparePeriodsToolCall
    | CompareActualBudgetToolCall
    | ContractStatusToolCall
    | DescribeSupportedFieldsToolCall,
    Field(discriminator="tool_name"),
]


class ToolAgentTurnEnvelope(StrictContractModel):
    schema_version: Literal["1.0"]
    tool_calls: list[ToolAgentToolCall] = Field(default_factory=list)
    final_request: SemanticRequestEnvelope | None = None
    cannot_answer: bool = False
    reason_code: OutOfScopeReasonCode | None = None

    @model_validator(mode="after")
    def validate_turn_shape(self) -> ToolAgentTurnEnvelope:
        if self.cannot_answer:
            if self.final_request is not None or self.tool_calls:
                raise ValueError("cannot_answer tool turns must not include calls or final_request")
            if self.reason_code is None:
                raise ValueError("cannot_answer tool turns require reason_code")
            return self
        if self.reason_code is not None:
            raise ValueError("reason_code is only valid when cannot_answer is true")
        if not self.tool_calls and self.final_request is None:
            raise ValueError("tool turns require at least one tool call or a final_request")
        return self


class ToolCallRecord(StrictContractModel):
    schema_version: Literal["1.0"]
    tool_name: CanonicalId
    arguments: dict[str, object]
    call_index: int = Field(ge=0)
    validation_outcome: ToolValidationOutcome
    result_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    duration_ms: int = Field(ge=0)
    error_code: str | None = None


class ProviderUsage(StrictContractModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)


class CostEstimate(StrictContractModel):
    estimated_usd: Decimal = Field(ge=Decimal("0"))
    currency: Literal["USD"] = "USD"


class ProviderHealth(StrictContractModel):
    status: ProviderHealthStatus
    provider: str = Field(min_length=1)
    detail: str | None = None


class ProviderRequest(StrictContractModel):
    schema_version: Literal["1.0"]
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    prompt_id: str = Field(min_length=1)
    prompt_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    system: str = Field(min_length=1)
    inputs: list[str] = Field(min_length=1)
    output_schema_ref: str = Field(min_length=1)
    output_schema_sha256: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    inference_parameters: dict[str, ScalarValue] = Field(default_factory=dict)
    timeout_seconds: int = Field(gt=0, le=300)
    metadata: dict[str, str] = Field(default_factory=dict)
    idempotency_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class ProviderResponse(StrictContractModel):
    schema_version: Literal["1.0"]
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    response_id: str = Field(min_length=1)
    finish_status: ProviderFinishStatus
    raw_payload: dict[str, object]
    parsed_payload: dict[str, object] | None = None
    usage: ProviderUsage
    cost: CostEstimate
    timing_ms: int = Field(ge=0)
    attempts: int = Field(ge=1)
    refusal: str | None = None

    @model_validator(mode="after")
    def validate_finish_payload(self) -> ProviderResponse:
        if self.finish_status is ProviderFinishStatus.STOP and self.parsed_payload is None:
            raise ValueError("successful provider responses require parsed_payload")
        if self.finish_status is ProviderFinishStatus.REFUSAL and self.refusal is None:
            raise ValueError("refusal provider responses require refusal text")
        return self


class PromptMetadata(StrictContractModel):
    schema_version: Literal["1.0"]
    prompt_id: str = Field(min_length=1)
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    approach: Approach
    locale_strategy: str = Field(min_length=1)
    expected_output_schema: str = Field(min_length=1)
    author: str = Field(min_length=1)
    reviewer: str = Field(min_length=1)
    changelog: list[str] = Field(min_length=1)
    template_file: str = Field(pattern=r"^[A-Za-z0-9_./-]+$")


class CanonicalResponse(StrictContractModel):
    schema_version: Literal["1.0"]
    outcome: ResultOutcome
    rows: list[dict[str, ScalarValue]] = Field(default_factory=list)
    units: dict[CanonicalId, str] = Field(default_factory=dict)
    assumptions: list[str] = Field(default_factory=list)
    message: LocalizedText | None = None
    clarification: ClarificationQuestion | None = None
    out_of_scope: OutOfScopeResponse | None = None


class ModelPricing(StrictContractModel):
    input_per_million_usd: Decimal = Field(ge=Decimal("0"))
    output_per_million_usd: Decimal = Field(ge=Decimal("0"))
    cached_input_per_million_usd: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))
    batch_input_per_million_usd: Decimal | None = Field(default=None, ge=Decimal("0"))
    batch_output_per_million_usd: Decimal | None = Field(default=None, ge=Decimal("0"))


class PriceTable(StrictContractModel):
    schema_version: Literal["1.0"]
    provider: str = Field(min_length=1)
    source: str = Field(min_length=1)
    checked_at_utc: datetime
    currency: Literal["USD"]
    model_prices: dict[str, ModelPricing] = Field(min_length=1)


class BudgetCheck(StrictContractModel):
    schema_version: Literal["1.0"]
    request_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    model: str = Field(min_length=1)
    estimated_input_tokens: int = Field(ge=0)
    estimated_output_tokens: int = Field(ge=0)
    safety_multiplier: Decimal = Field(ge=Decimal("1"))
    estimated_usd: Decimal = Field(ge=Decimal("0"))
    run_budget_usd: Decimal = Field(ge=Decimal("0"))
    monthly_limit_usd: Decimal = Field(ge=Decimal("0"))
    remaining_run_budget_usd: Decimal
    remaining_monthly_budget_usd: Decimal
    price_checked_at_utc: datetime
