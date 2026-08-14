"""Execution-design helpers for primary and stability F7 runs."""

from __future__ import annotations

import hashlib
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

from semplan.benchmark.validator import load_benchmark_cases
from semplan.contracts import (
    BenchmarkCase,
    DatasetSplit,
    ExecutionDesign,
    ExpectedPolicy,
    QuestionClass,
)
from semplan.data_generation.writer import canonical_json
from semplan.errors import ErrorCode, ProjectError

F7_COST_SAFE_DESIGN_ID = "f7-primary-plus-stability-v1"
F7_STABILITY_SAMPLING_ALGORITHM = "marginal-balanced-greedy-swap-v2"
F7_STABILITY_SAMPLING_SEED = 20260811
F7_STABILITY_CASE_COUNT = 150
F7_PRIMARY_REPETITIONS = 1
F7_STABILITY_ADDITIONAL_REPETITIONS = 2

REPRESENTATION_DIMENSIONS: tuple[str, ...] = (
    "language",
    "split",
    "taxonomy",
    "difficulty",
    "turn_scope",
    "case_role",
    "expected_policy",
    "operation",
)
SCIENTIFIC_SPLITS: tuple[DatasetSplit, ...] = (
    DatasetSplit.TEST_PUBLIC,
    DatasetSplit.TEST_HIDDEN,
    DatasetSplit.MULTI_TURN,
    DatasetSplit.ADVERSARIAL,
)
_DIMENSION_WEIGHTS: dict[str, Decimal] = {
    "language": Decimal("4"),
    "split": Decimal("4"),
    "taxonomy": Decimal("3"),
    "difficulty": Decimal("3"),
    "turn_scope": Decimal("3"),
    "case_role": Decimal("3"),
    "expected_policy": Decimal("2"),
    "operation": Decimal("2"),
}
_DISTRIBUTION_TOLERANCES = {
    "language": Decimal("0"),
    "split": Decimal("1"),
    "taxonomy": Decimal("3"),
    "difficulty": Decimal("2"),
    "turn_scope": Decimal("1"),
    "case_role": Decimal("2"),
    "expected_policy": Decimal("2"),
    "operation": Decimal("2"),
}


def scientific_cases(cases: list[BenchmarkCase]) -> list[BenchmarkCase]:
    """Return release-scale scientific cases in deterministic case-id order."""

    return sorted((case for case in cases if case.split in SCIENTIFIC_SPLITS), key=_case_id)


def build_cost_safe_execution_design(
    cases: list[BenchmarkCase],
    *,
    stability_case_count: int = F7_STABILITY_CASE_COUNT,
    stability_seed: int = F7_STABILITY_SAMPLING_SEED,
    primary_repetitions: int = F7_PRIMARY_REPETITIONS,
    stability_additional_repetitions: int = F7_STABILITY_ADDITIONAL_REPETITIONS,
) -> ExecutionDesign:
    """Build the preregistered F7 primary-plus-stability repetition design."""

    selected_scientific_cases = scientific_cases(cases)
    if stability_case_count > len(selected_scientific_cases):
        raise ProjectError(
            ErrorCode.CFG_INVALID,
            "Stability subset cannot exceed scientific case count",
            detail={
                "stability_case_count": stability_case_count,
                "scientific_case_count": len(selected_scientific_cases),
            },
        )
    stability_cases = select_stability_subset(
        selected_scientific_cases,
        count=stability_case_count,
        seed=stability_seed,
    )
    stability_case_ids = [case.case_id for case in stability_cases]
    return ExecutionDesign(
        schema_version="1.0",
        design_id=F7_COST_SAFE_DESIGN_ID,
        analysis_plan=(
            "Primary comparisons use repetition 1 only over all scientific cases. "
            "Additional repetitions 2..3 are restricted to the deterministic stability subset "
            "and analyzed only for repeatability; they are not independent primary examples."
        ),
        scientific_case_ids=[case.case_id for case in selected_scientific_cases],
        primary_repetitions=primary_repetitions,
        stability_subset_case_ids=stability_case_ids,
        stability_additional_repetitions=stability_additional_repetitions,
        stability_sampling_seed=stability_seed,
        stability_sampling_algorithm=F7_STABILITY_SAMPLING_ALGORITHM,
        stability_subset_sha256=stability_subset_sha256(stability_case_ids, seed=stability_seed),
    )


def select_stability_subset(
    cases: list[BenchmarkCase],
    *,
    count: int,
    seed: int,
) -> list[BenchmarkCase]:
    """Select a deterministic representative subset by minimizing marginal deficits."""

    if count < 1:
        raise ProjectError(ErrorCode.CFG_INVALID, "Stability subset count must be positive")
    ordered_cases = sorted(cases, key=_case_id)
    full_distributions = distribution_counts(ordered_cases)
    targets = {
        dimension: {
            value: Decimal(full_count) * Decimal(count) / Decimal(len(ordered_cases))
            for value, full_count in counts.items()
        }
        for dimension, counts in full_distributions.items()
    }
    selected: list[BenchmarkCase] = []
    selected_ids: set[str] = set()
    selected_counts = {dimension: Counter[str]() for dimension in REPRESENTATION_DIMENSIONS}

    for _ in range(count):
        remaining = [case for case in ordered_cases if case.case_id not in selected_ids]
        best = min(
            remaining,
            key=lambda case: (
                _candidate_score(case, selected_counts, targets),
                _stable_tiebreak(seed, case.case_id),
                case.case_id,
            ),
        )
        selected.append(best)
        selected_ids.add(best.case_id)
        for dimension, value in case_dimensions(best).items():
            selected_counts[dimension][value] += 1
    return sorted(_rebalance_subset(selected, ordered_cases, seed=seed), key=_case_id)


def stability_subset_sha256(case_ids: list[str], *, seed: int) -> str:
    payload = {
        "schema_version": "1.0",
        "algorithm": F7_STABILITY_SAMPLING_ALGORITHM,
        "seed": seed,
        "case_ids": case_ids,
    }
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def validate_stability_execution_design(
    *,
    benchmark_dir: Path,
    manifest_execution_design: ExecutionDesign,
    expected_stability_count: int = F7_STABILITY_CASE_COUNT,
) -> dict[str, Any]:
    cases = load_benchmark_cases(benchmark_dir)
    case_map = {case.case_id: case for case in cases}
    scientific_case_ids = [case.case_id for case in scientific_cases(cases)]
    stability_ids = manifest_execution_design.stability_subset_case_ids

    errors: list[str] = []
    if manifest_execution_design.scientific_case_ids != scientific_case_ids:
        errors.append("scientific_case_ids do not match benchmark scientific splits")
    if len(stability_ids) != expected_stability_count:
        errors.append(
            f"expected {expected_stability_count} stability cases, found {len(stability_ids)}"
        )
    if len(set(stability_ids)) != len(stability_ids):
        errors.append("stability subset contains duplicate case IDs")
    missing = sorted(set(stability_ids).difference(scientific_case_ids))
    if missing:
        errors.append(f"stability subset contains non-scientific case IDs: {missing[:5]}")
    expected_hash = stability_subset_sha256(
        stability_ids,
        seed=manifest_execution_design.stability_sampling_seed or 0,
    )
    if manifest_execution_design.stability_subset_sha256 != expected_hash:
        errors.append("stability subset SHA-256 does not match selected case IDs")

    full_cases = [case_map[case_id] for case_id in scientific_case_ids]
    subset_cases = [case_map[case_id] for case_id in stability_ids if case_id in case_map]
    comparison = distribution_comparison(full_cases, subset_cases)
    errors.extend(_distribution_errors(comparison))

    return {
        "schema_version": "1.0",
        "status": "failed" if errors else "passed",
        "ok": not errors,
        "errors": errors,
        "algorithm": manifest_execution_design.stability_sampling_algorithm,
        "seed": manifest_execution_design.stability_sampling_seed,
        "scientific_case_count": len(scientific_case_ids),
        "stability_case_count": len(stability_ids),
        "stability_subset_sha256": manifest_execution_design.stability_subset_sha256,
        "full_distribution": distribution_counts(full_cases),
        "subset_distribution": distribution_counts(subset_cases),
        "distribution_comparison": comparison,
    }


def distribution_counts(cases: list[BenchmarkCase]) -> dict[str, dict[str, int]]:
    counters = {dimension: Counter[str]() for dimension in REPRESENTATION_DIMENSIONS}
    for case in cases:
        for dimension, value in case_dimensions(case).items():
            counters[dimension][value] += 1
    return {dimension: dict(sorted(counter.items())) for dimension, counter in counters.items()}


def distribution_comparison(
    full_cases: list[BenchmarkCase],
    subset_cases: list[BenchmarkCase],
) -> dict[str, dict[str, dict[str, str | int]]]:
    full = distribution_counts(full_cases)
    subset = distribution_counts(subset_cases)
    scale = Decimal(len(subset_cases)) / Decimal(len(full_cases))
    comparison: dict[str, dict[str, dict[str, str | int]]] = {}
    for dimension in REPRESENTATION_DIMENSIONS:
        rows: dict[str, dict[str, str | int]] = {}
        values = sorted(set(full[dimension]).union(subset[dimension]))
        for value in values:
            full_count = full[dimension].get(value, 0)
            subset_count = subset[dimension].get(value, 0)
            expected = Decimal(full_count) * scale
            rows[value] = {
                "full_count": full_count,
                "subset_count": subset_count,
                "expected_subset_count": str(expected.quantize(Decimal("0.001"))),
                "absolute_delta": str((Decimal(subset_count) - expected).copy_abs()),
            }
        comparison[dimension] = rows
    return comparison


def case_dimensions(case: BenchmarkCase) -> dict[str, str]:
    return {
        "language": case.language.value,
        "split": case.split.value,
        "taxonomy": case.intent.value,
        "difficulty": case.difficulty.value,
        "turn_scope": "multi_turn" if case.split is DatasetSplit.MULTI_TURN else "single_turn",
        "case_role": _case_role(case),
        "expected_policy": case.expected_policy.value,
        "operation": case.expected_operation.value,
    }


def _case_role(case: BenchmarkCase) -> str:
    if case.split is DatasetSplit.ADVERSARIAL:
        return "adversarial"
    if case.requires_clarification or case.expected_policy is ExpectedPolicy.CLARIFY:
        return "clarification"
    if case.intent is QuestionClass.OUT_OF_SCOPE or case.expected_policy in {
        ExpectedPolicy.OUT_OF_SCOPE,
        ExpectedPolicy.POLICY_VIOLATION,
    }:
        return "out_of_scope"
    return "normal"


def _candidate_score(
    case: BenchmarkCase,
    selected_counts: dict[str, Counter[str]],
    targets: dict[str, dict[str, Decimal]],
) -> Decimal:
    dimensions = case_dimensions(case)
    score = Decimal("0")
    for dimension, values in targets.items():
        value = dimensions[dimension]
        current = Decimal(selected_counts[dimension][value])
        target = values[value]
        before = (target - current).copy_abs()
        after = (target - current - Decimal("1")).copy_abs()
        improvement = before - after
        normalized = improvement / max(target, Decimal("1"))
        score -= _DIMENSION_WEIGHTS[dimension] * normalized
    return score


def _rebalance_subset(
    selected: list[BenchmarkCase],
    cases: list[BenchmarkCase],
    *,
    seed: int,
) -> list[BenchmarkCase]:
    full_counts = _counts_for_cases(cases)
    targets = _targets(full_counts, subset_size=len(selected), full_size=len(cases))
    selected_ids = {case.case_id for case in selected}
    selected_counts = _counts_for_cases(selected)

    for _ in range(100):
        rebalanced_dimension = _worst_overflow_dimension(selected_counts, targets)
        if rebalanced_dimension is None:
            break
        over_value = max(
            targets[rebalanced_dimension],
            key=lambda value: (
                Decimal(selected_counts[rebalanced_dimension][value])
                - targets[rebalanced_dimension][value]
            ),
        )
        under_value = min(
            targets[rebalanced_dimension],
            key=lambda value: (
                Decimal(selected_counts[rebalanced_dimension][value])
                - targets[rebalanced_dimension][value]
            ),
        )
        surplus = (
            Decimal(selected_counts[rebalanced_dimension][over_value])
            - targets[rebalanced_dimension][over_value]
        )
        deficit = targets[rebalanced_dimension][under_value] - Decimal(
            selected_counts[rebalanced_dimension][under_value]
        )
        if surplus <= Decimal("0") or deficit <= Decimal("0"):
            break

        outgoing = [
            case for case in selected if case_dimensions(case)[rebalanced_dimension] == over_value
        ]
        incoming = [
            case
            for case in cases
            if case.case_id not in selected_ids
            and case_dimensions(case)[rebalanced_dimension] == under_value
        ]
        if not outgoing or not incoming:
            break

        current_objective = _distribution_objective(selected_counts, targets)
        best_swap: tuple[BenchmarkCase, BenchmarkCase] | None = None
        best_key: tuple[Decimal, str, str, str] | None = None
        for out_case in outgoing:
            for in_case in incoming:
                candidate_objective = _swap_objective(
                    selected_counts,
                    targets,
                    out_case,
                    in_case,
                )
                key = (
                    candidate_objective,
                    _stable_tiebreak(seed, f"{out_case.case_id}:{in_case.case_id}"),
                    out_case.case_id,
                    in_case.case_id,
                )
                if best_key is None or key < best_key:
                    best_key = key
                    best_swap = (out_case, in_case)
        if best_swap is None or best_key is None or best_key[0] >= current_objective:
            break

        out_case, in_case = best_swap
        selected = [in_case if case.case_id == out_case.case_id else case for case in selected]
        selected_ids.remove(out_case.case_id)
        selected_ids.add(in_case.case_id)
        selected_counts = _counts_for_cases(selected)
    return selected


def _counts_for_cases(cases: list[BenchmarkCase]) -> dict[str, Counter[str]]:
    counters = {dimension: Counter[str]() for dimension in REPRESENTATION_DIMENSIONS}
    for case in cases:
        for dimension, value in case_dimensions(case).items():
            counters[dimension][value] += 1
    return counters


def _targets(
    counts: dict[str, Counter[str]],
    *,
    subset_size: int,
    full_size: int,
) -> dict[str, dict[str, Decimal]]:
    return {
        dimension: {
            value: Decimal(full_count) * Decimal(subset_size) / Decimal(full_size)
            for value, full_count in dimension_counts.items()
        }
        for dimension, dimension_counts in counts.items()
    }


def _worst_overflow_dimension(
    counts: dict[str, Counter[str]],
    targets: dict[str, dict[str, Decimal]],
) -> str | None:
    worst_dimension = None
    worst_overflow = Decimal("0")
    for dimension, values in targets.items():
        tolerance = _DISTRIBUTION_TOLERANCES[dimension]
        for value, target in values.items():
            delta = (Decimal(counts[dimension][value]) - target).copy_abs()
            overflow = delta - tolerance
            if overflow > worst_overflow:
                worst_overflow = overflow
                worst_dimension = dimension
    return worst_dimension


def _distribution_objective(
    counts: dict[str, Counter[str]],
    targets: dict[str, dict[str, Decimal]],
) -> Decimal:
    score = Decimal("0")
    for dimension, values in targets.items():
        for value, target in values.items():
            delta = (Decimal(counts[dimension][value]) - target).copy_abs()
            normalized = delta / max(target, Decimal("1"))
            overflow = max(Decimal("0"), delta - _DISTRIBUTION_TOLERANCES[dimension])
            score += _DIMENSION_WEIGHTS[dimension] * normalized * normalized
            score += Decimal("1000") * overflow * overflow
    return score


def _swap_objective(
    counts: dict[str, Counter[str]],
    targets: dict[str, dict[str, Decimal]],
    out_case: BenchmarkCase,
    in_case: BenchmarkCase,
) -> Decimal:
    out_dimensions = case_dimensions(out_case)
    in_dimensions = case_dimensions(in_case)
    score = Decimal("0")
    for dimension, values in targets.items():
        for value, target in values.items():
            adjusted = Decimal(counts[dimension][value])
            if out_dimensions[dimension] == value:
                adjusted -= 1
            if in_dimensions[dimension] == value:
                adjusted += 1
            delta = (adjusted - target).copy_abs()
            normalized = delta / max(target, Decimal("1"))
            overflow = max(Decimal("0"), delta - _DISTRIBUTION_TOLERANCES[dimension])
            score += _DIMENSION_WEIGHTS[dimension] * normalized * normalized
            score += Decimal("1000") * overflow * overflow
    return score


def _stable_tiebreak(seed: int, case_id: str) -> str:
    payload = f"{seed}:{case_id}".encode()
    return hashlib.sha256(payload).hexdigest()


def _distribution_errors(
    comparison: dict[str, dict[str, dict[str, str | int]]],
) -> list[str]:
    errors: list[str] = []
    for dimension, values in comparison.items():
        tolerance = _DISTRIBUTION_TOLERANCES[dimension]
        for value, row in values.items():
            delta = Decimal(str(row["absolute_delta"]))
            if delta > tolerance:
                errors.append(
                    f"{dimension}={value} subset delta {delta} exceeds tolerance {tolerance}"
                )
    return errors


def _case_id(case: BenchmarkCase) -> str:
    return case.case_id
