from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from semplan.contracts import (
    AnalysisRole,
    Approach,
    ArtifactRef,
    ExpectedPolicy,
    Locale,
    PredictionStatus,
    ProviderUsage,
    ResultExecutionRef,
    ResultInputRef,
    ResultOutcome,
    ResultPredictionRef,
    ResultProviderRef,
    ResultRecord,
    ScoreSummary,
)
from semplan.experiments.statistics import (
    approach_metric_rates,
    mcnemar_exact_p_value,
    paired_binary_contrasts,
    paired_bootstrap_contrasts,
    primary_analysis_records,
    stability_repeatability_summary,
)


def test_mcnemar_exact_p_value_uses_two_sided_binomial_tail() -> None:
    assert mcnemar_exact_p_value(1, 3) == Decimal("0.625")


def test_paired_binary_contrasts_report_risk_difference_and_holm() -> None:
    records = [
        _record("c1", Approach.A3, True, Decimal("0")),
        _record("c1", Approach.A1, False, Decimal("0")),
        _record("c2", Approach.A3, True, Decimal("0")),
        _record("c2", Approach.A1, True, Decimal("0")),
        _record("c3", Approach.A3, True, Decimal("0")),
        _record("c3", Approach.A1, False, Decimal("0")),
    ]

    rows = paired_binary_contrasts(
        records,
        metric="answer_correct",
        comparisons=((Approach.A3, Approach.A1),),
    )

    assert rows[0]["n"] == 3
    assert rows[0]["mcnemar_b"] == 2
    assert rows[0]["mcnemar_c"] == 0
    assert rows[0]["risk_difference"] == Decimal("0.666667")
    assert rows[0]["holm_adjusted_p"] == rows[0]["p_value"]


def test_paired_bootstrap_contrasts_are_seeded() -> None:
    records = [
        _record("c1", Approach.A3, True, Decimal("1")),
        _record("c1", Approach.A1, True, Decimal("2")),
        _record("c2", Approach.A3, True, Decimal("3")),
        _record("c2", Approach.A1, True, Decimal("5")),
    ]

    left = paired_bootstrap_contrasts(
        records,
        metric="cost_usd",
        resamples=100,
        seed=7,
        comparisons=((Approach.A3, Approach.A1),),
    )
    right = paired_bootstrap_contrasts(
        records,
        metric="cost_usd",
        resamples=100,
        seed=7,
        comparisons=((Approach.A3, Approach.A1),),
    )

    assert left == right
    assert left[0]["mean_difference"] == Decimal("-1.500000")


def test_primary_analysis_records_exclude_stability_repetitions() -> None:
    records = [
        _record("c1", Approach.A3, True, Decimal("0"), analysis_role=AnalysisRole.PRIMARY),
        _record("c2", Approach.A3, False, Decimal("0"), analysis_role=AnalysisRole.PRIMARY),
        _record("c1", Approach.A3, False, Decimal("0"), analysis_role=AnalysisRole.STABILITY),
        _record("c1", Approach.A3, False, Decimal("0"), analysis_role=AnalysisRole.STABILITY),
    ]

    primary_rows = approach_metric_rates(primary_analysis_records(records))
    stability_rows = stability_repeatability_summary(records)

    assert primary_rows[0]["n"] == 2
    assert primary_rows[0]["answer_correct_rate"] == "0.500000"
    assert stability_rows[0]["stability_records"] == 2


def _record(
    case_id: str,
    approach: Approach,
    answer_correct: bool,
    cost_usd: Decimal,
    *,
    analysis_role: AnalysisRole = AnalysisRole.PRIMARY,
) -> ResultRecord:
    suffix = (case_id + approach.value).lower().replace("-", "")
    digest = (suffix + ("a" * 64))[:64]
    artifact = ArtifactRef(path=f"raw/{digest}.json", sha256="sha256:" + ("b" * 64))
    return ResultRecord(
        schema_version="1.0",
        run_id="unit-run",
        work_item_id="sha256:" + digest,
        case_id=case_id,
        approach=approach,
        repetition=1,
        analysis_role=analysis_role,
        input=ResultInputRef(
            utterance_sha256="sha256:" + ("c" * 64),
            state_sha256=None,
            split="development",
            language=Locale.EN_US,
        ),
        provider=ResultProviderRef(
            request_sha256="sha256:" + ("d" * 64),
            request_ref=artifact,
            response_ref=artifact,
            model_requested="fake",
            model_returned="fake",
            usage=ProviderUsage(input_tokens=1, output_tokens=1),
            cost_usd=cost_usd,
        ),
        prediction=ResultPredictionRef(status=PredictionStatus.PARSED, artifact_ref=artifact),
        execution=ResultExecutionRef(
            policy=ExpectedPolicy.ALLOW,
            executed_database=True,
            query_sha256="sha256:" + ("e" * 64),
            duration_ms=0,
            row_count=1,
            result_ref=artifact,
        ),
        scores=ScoreSummary(
            answer_correct=answer_correct,
            unsafe_or_invalid=False,
            semantic_exact=True,
            semantic_component_precision=Decimal("1"),
            semantic_component_recall=Decimal("1"),
            semantic_component_f1=Decimal("1"),
            execution_success=True,
            clarification_decision_correct=None,
            sequence_state_correct=None,
            policy_correct=answer_correct,
            false_refusal=False,
            cost_usd=cost_usd,
            latency_ms=0,
            provider_latency_ms=0,
            input_tokens=1,
            output_tokens=1,
        ),
        score_ref=artifact,
        errors=[],
        timestamps={"completed_at": datetime(2026, 8, 6, tzinfo=UTC)},
        outcome=ResultOutcome.ANSWERED,
    )
