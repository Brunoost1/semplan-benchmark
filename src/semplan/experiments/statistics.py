"""Deterministic paired statistics for experiment result records."""

from __future__ import annotations

import math
import random
from collections import defaultdict
from collections.abc import Iterable
from decimal import Decimal
from statistics import mean, median
from typing import Any

from semplan.contracts import AnalysisRole, Approach, ResultRecord

PRIMARY_BINARY_CONTRASTS: tuple[tuple[Approach, Approach], ...] = (
    (Approach.A3, Approach.A1),
    (Approach.A4, Approach.A1),
    (Approach.A3, Approach.A2),
    (Approach.A4, Approach.A2),
    (Approach.A4, Approach.A3),
)


def approach_metric_rates(records: Iterable[ResultRecord]) -> list[dict[str, Any]]:
    grouped: dict[Approach, list[ResultRecord]] = defaultdict(list)
    for record in records:
        grouped[record.approach].append(record)

    rows: list[dict[str, Any]] = []
    for approach in sorted(grouped, key=lambda item: item.value):
        approach_records = grouped[approach]
        rows.append(
            {
                "approach": approach.value,
                "n": len(approach_records),
                "answer_correct_rate": _binary_rate(approach_records, "answer_correct"),
                "policy_correct_rate": _binary_rate(approach_records, "policy_correct"),
                "unsafe_or_invalid_rate": _binary_rate(approach_records, "unsafe_or_invalid"),
                "false_refusal_rate": _binary_rate(approach_records, "false_refusal"),
                "mean_cost_usd": _mean_decimal(approach_records, "cost_usd"),
                "median_cost_usd": _median_decimal(approach_records, "cost_usd"),
                "mean_latency_ms": _mean_decimal(approach_records, "latency_ms"),
                "median_latency_ms": _median_decimal(approach_records, "latency_ms"),
            }
        )
    return rows


def primary_analysis_records(records: Iterable[ResultRecord]) -> list[ResultRecord]:
    """Records eligible for primary case-level analysis."""

    return [record for record in records if record.analysis_role is AnalysisRole.PRIMARY]


def stability_analysis_records(records: Iterable[ResultRecord]) -> list[ResultRecord]:
    """Records eligible for repeatability-only analysis."""

    return [record for record in records if record.analysis_role is AnalysisRole.STABILITY]


def stability_repeatability_summary(records: Iterable[ResultRecord]) -> list[dict[str, Any]]:
    grouped: dict[tuple[Approach, str], list[ResultRecord]] = defaultdict(list)
    for record in stability_analysis_records(records):
        grouped[(record.approach, record.case_id)].append(record)

    by_approach: dict[Approach, list[list[ResultRecord]]] = defaultdict(list)
    for (approach, _case_id), case_records in grouped.items():
        by_approach[approach].append(case_records)

    rows: list[dict[str, Any]] = []
    for approach, case_groups in sorted(by_approach.items(), key=lambda item: item[0].value):
        run_counts = [len(case_records) for case_records in case_groups]
        correctness_agreement = [
            _all_equal(
                [
                    record.scores.answer_correct
                    for record in case_records
                    if record.scores.answer_correct is not None
                ]
            )
            for case_records in case_groups
        ]
        rows.append(
            {
                "approach": approach.value,
                "stability_cases": len(case_groups),
                "stability_records": sum(run_counts),
                "min_additional_runs_per_case": min(run_counts) if run_counts else 0,
                "max_additional_runs_per_case": max(run_counts) if run_counts else 0,
                "answer_correct_repeatability_rate": str(
                    _quantize_rate(_rate_from_values(correctness_agreement))
                )
                if correctness_agreement
                else "",
            }
        )
    return rows


def paired_binary_contrasts(
    records: Iterable[ResultRecord],
    *,
    metric: str,
    comparisons: tuple[tuple[Approach, Approach], ...] = PRIMARY_BINARY_CONTRASTS,
) -> list[dict[str, Any]]:
    by_case = _binary_case_values(records, metric)
    rows: list[dict[str, Any]] = []
    for left, right in comparisons:
        left_values = by_case.get(left, {})
        right_values = by_case.get(right, {})
        paired_cases = sorted(set(left_values).intersection(right_values))
        pairs: list[tuple[bool, bool]] = []
        for case_id in paired_cases:
            left_value = left_values[case_id]
            right_value = right_values[case_id]
            if left_value is not None and right_value is not None:
                pairs.append((left_value, right_value))
        n = len(pairs)
        b = sum(1 for left_value, right_value in pairs if left_value and not right_value)
        c = sum(1 for left_value, right_value in pairs if right_value and not left_value)
        left_rate = _rate_from_values(left_value for left_value, _ in pairs)
        right_rate = _rate_from_values(right_value for _, right_value in pairs)
        risk_difference = left_rate - right_rate
        ci_low, ci_high = _paired_risk_difference_ci(b, c, n)
        rows.append(
            {
                "metric": metric,
                "left": left.value,
                "right": right.value,
                "n": n,
                "left_rate": _quantize_rate(left_rate),
                "right_rate": _quantize_rate(right_rate),
                "risk_difference": _quantize_rate(risk_difference),
                "ci95_low": _quantize_rate(ci_low),
                "ci95_high": _quantize_rate(ci_high),
                "mcnemar_b": b,
                "mcnemar_c": c,
                "p_value": _quantize_p(mcnemar_exact_p_value(b, c)),
            }
        )
    return _apply_holm(rows)


def paired_bootstrap_contrasts(
    records: Iterable[ResultRecord],
    *,
    metric: str,
    resamples: int = 10_000,
    seed: int = 20260806,
    comparisons: tuple[tuple[Approach, Approach], ...] = PRIMARY_BINARY_CONTRASTS[:4],
) -> list[dict[str, Any]]:
    by_case = _continuous_case_values(records, metric)
    rng = random.Random(seed)
    rows: list[dict[str, Any]] = []
    for left, right in comparisons:
        paired_cases = sorted(set(by_case.get(left, {})).intersection(by_case.get(right, {})))
        pairs = [(by_case[left][case_id], by_case[right][case_id]) for case_id in paired_cases]
        if not pairs:
            rows.append(_empty_bootstrap_row(metric, left, right))
            continue
        diffs = [left_value - right_value for left_value, right_value in pairs]
        ratios = [
            left_value / right_value
            for left_value, right_value in pairs
            if right_value != Decimal("0")
        ]
        bootstrap_means: list[Decimal] = []
        for _ in range(resamples):
            sample = [diffs[rng.randrange(len(diffs))] for _ in diffs]
            bootstrap_means.append(sum(sample, Decimal("0")) / Decimal(len(sample)))
        low, high = _quantile_interval(bootstrap_means)
        rows.append(
            {
                "metric": metric,
                "left": left.value,
                "right": right.value,
                "n": len(pairs),
                "left_mean": _quantize_decimal(mean(left_value for left_value, _ in pairs)),
                "right_mean": _quantize_decimal(mean(right_value for _, right_value in pairs)),
                "mean_difference": _quantize_decimal(mean(diffs)),
                "median_difference": _quantize_decimal(median(diffs)),
                "mean_ratio": _quantize_decimal(mean(ratios)) if ratios else None,
                "ci95_low": _quantize_decimal(low),
                "ci95_high": _quantize_decimal(high),
            }
        )
    return rows


def mcnemar_exact_p_value(b: int, c: int) -> Decimal:
    discordant = b + c
    if discordant == 0:
        return Decimal("1")
    tail = sum(math.comb(discordant, index) for index in range(0, min(b, c) + 1))
    probability = Decimal(2 * tail) / Decimal(2**discordant)
    return min(Decimal("1"), probability)


def _apply_holm(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sorted_rows = sorted(enumerate(rows), key=lambda item: Decimal(str(item[1]["p_value"])))
    adjusted: dict[int, Decimal] = {}
    running_max = Decimal("0")
    total = len(rows)
    for rank, (original_index, row) in enumerate(sorted_rows, start=1):
        raw = Decimal(str(row["p_value"]))
        candidate = min(Decimal("1"), raw * Decimal(total - rank + 1))
        running_max = max(running_max, candidate)
        adjusted[original_index] = running_max
    for index, row in enumerate(rows):
        row["holm_adjusted_p"] = _quantize_p(adjusted[index])
    return rows


def _binary_case_values(
    records: Iterable[ResultRecord],
    metric: str,
) -> dict[Approach, dict[str, bool | None]]:
    values: dict[Approach, dict[str, list[bool | None]]] = defaultdict(lambda: defaultdict(list))
    for record in records:
        value = getattr(record.scores, metric)
        values[record.approach][record.case_id].append(value)
    summarized: dict[Approach, dict[str, bool | None]] = defaultdict(dict)
    for approach, by_case in values.items():
        for case_id, case_values in by_case.items():
            concrete = [value for value in case_values if value is not None]
            if not concrete:
                summarized[approach][case_id] = None
            else:
                summarized[approach][case_id] = sum(concrete) > len(concrete) / 2
    return summarized


def _continuous_case_values(
    records: Iterable[ResultRecord],
    metric: str,
) -> dict[Approach, dict[str, Decimal]]:
    values: dict[Approach, dict[str, list[Decimal]]] = defaultdict(lambda: defaultdict(list))
    for record in records:
        raw = getattr(record.scores, metric)
        values[record.approach][record.case_id].append(Decimal(str(raw)))
    return {
        approach: {
            case_id: sum(case_values, Decimal("0")) / Decimal(len(case_values))
            for case_id, case_values in by_case.items()
        }
        for approach, by_case in values.items()
    }


def _binary_rate(records: list[ResultRecord], metric: str) -> str:
    values = [getattr(record.scores, metric) for record in records]
    concrete = [value for value in values if value is not None]
    if not concrete:
        return ""
    return str(_quantize_rate(_rate_from_values(concrete)))


def _mean_decimal(records: list[ResultRecord], metric: str) -> str:
    values = [Decimal(str(getattr(record.scores, metric))) for record in records]
    return str(_quantize_decimal(mean(values))) if values else ""


def _median_decimal(records: list[ResultRecord], metric: str) -> str:
    values = [Decimal(str(getattr(record.scores, metric))) for record in records]
    return str(_quantize_decimal(median(values))) if values else ""


def _rate_from_values(values: Iterable[bool]) -> Decimal:
    concrete = list(values)
    if not concrete:
        return Decimal("0")
    return Decimal(sum(concrete)) / Decimal(len(concrete))


def _all_equal(values: list[bool]) -> bool:
    return len(set(values)) <= 1


def _paired_risk_difference_ci(b: int, c: int, n: int) -> tuple[Decimal, Decimal]:
    if n == 0:
        return Decimal("0"), Decimal("0")
    diff = Decimal(b - c) / Decimal(n)
    variance = Decimal(max(0, b + c - ((b - c) ** 2 / n))) / Decimal(n * n)
    margin = Decimal("1.96") * Decimal(str(math.sqrt(float(variance))))
    return max(Decimal("-1"), diff - margin), min(Decimal("1"), diff + margin)


def _quantile_interval(values: list[Decimal]) -> tuple[Decimal, Decimal]:
    ordered = sorted(values)
    low_index = max(0, int(len(ordered) * 0.025) - 1)
    high_index = min(len(ordered) - 1, int(len(ordered) * 0.975))
    return ordered[low_index], ordered[high_index]


def _empty_bootstrap_row(metric: str, left: Approach, right: Approach) -> dict[str, Any]:
    return {
        "metric": metric,
        "left": left.value,
        "right": right.value,
        "n": 0,
        "left_mean": None,
        "right_mean": None,
        "mean_difference": None,
        "median_difference": None,
        "mean_ratio": None,
        "ci95_low": None,
        "ci95_high": None,
    }


def _quantize_rate(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.000001"))


def _quantize_p(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.000001"))


def _quantize_decimal(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.000001"))
