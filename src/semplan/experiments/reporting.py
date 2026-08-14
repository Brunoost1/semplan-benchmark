"""Generate F6/F7 analysis tables, figures, and run reports from result records."""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

from semplan.benchmark.validator import load_benchmark_cases
from semplan.contracts import Approach, BenchmarkCase, DatasetSplit, ResultRecord
from semplan.data_generation.writer import canonical_json, sha256_file
from semplan.experiments.artifacts import read_result_records, validate_experiment_directory
from semplan.experiments.manifest import validate_manifest_copy
from semplan.experiments.scoring import write_metric_dictionary
from semplan.experiments.statistics import (
    approach_metric_rates,
    paired_binary_contrasts,
    paired_bootstrap_contrasts,
    primary_analysis_records,
    stability_repeatability_summary,
)


def generate_analysis_artifacts(*, run_dir: Path, benchmark_dir: Path) -> dict[str, Any]:
    validation = validate_experiment_directory(run_dir)
    manifest = validate_manifest_copy(run_dir)
    records = read_result_records(run_dir)
    primary_records = primary_analysis_records(records)
    cases = load_benchmark_cases(benchmark_dir)
    case_map = {case.case_id: case for case in cases}

    tables_dir = run_dir / "tables"
    figures_dir = run_dir / "figures"
    tables_dir.mkdir(parents=True, exist_ok=True)
    figures_dir.mkdir(parents=True, exist_ok=True)

    table_refs = {
        "metrics_dictionary": _write_metric_dictionary(tables_dir / "metrics_dictionary.csv"),
        "dataset_composition": _write_csv(
            tables_dir / "dataset_composition.csv",
            _dataset_composition_rows(cases),
        ),
        "approach_configuration": _write_csv(
            tables_dir / "approach_configuration.csv",
            _approach_configuration_rows(manifest),
        ),
        "primary_correctness": _write_csv(
            tables_dir / "primary_correctness.csv",
            approach_metric_rates(primary_records),
        ),
        "failure_policy_outcomes": _write_csv(
            tables_dir / "failure_policy_outcomes.csv",
            _failure_policy_rows(primary_records),
        ),
        "cost_latency": _write_csv(
            tables_dir / "cost_latency.csv",
            _cost_latency_rows(primary_records),
        ),
        "ambiguity_results": _write_csv(
            tables_dir / "ambiguity_results.csv",
            _ambiguity_rows(primary_records, case_map),
        ),
        "multi_turn_results": _write_csv(
            tables_dir / "multi_turn_results.csv",
            _multi_turn_rows(primary_records, case_map),
        ),
        "subgroup_results": _write_csv(
            tables_dir / "subgroup_results.csv",
            _subgroup_rows(primary_records, case_map),
        ),
        "contrasts": _write_csv(tables_dir / "contrasts.csv", _contrast_rows(primary_records)),
        "stability_repeatability": _write_csv(
            tables_dir / "stability_repeatability.csv",
            stability_repeatability_summary(records),
        ),
    }

    rates = approach_metric_rates(primary_records)
    figure_refs = {
        "correctness_intervals": _write_svg(
            figures_dir / "correctness_intervals.svg",
            _bar_svg(
                title="Answer correctness with paired-analysis context",
                values=[
                    (row["approach"], Decimal(str(row["answer_correct_rate"] or "0")))
                    for row in rates
                ],
                unit="rate",
            ),
        ),
        "unsafe_invalid_rates": _write_svg(
            figures_dir / "unsafe_invalid_rates.svg",
            _bar_svg(
                title="Unsafe or invalid rate",
                values=[
                    (row["approach"], Decimal(str(row["unsafe_or_invalid_rate"] or "0")))
                    for row in rates
                ],
                unit="rate",
            ),
        ),
        "cost_distribution": _write_svg(
            figures_dir / "cost_distribution.svg",
            _bar_svg(
                title="Mean provider cost by approach (USD)",
                values=[
                    (row["approach"], Decimal(str(row["mean_cost_usd"] or "0"))) for row in rates
                ],
                unit="usd",
            ),
        ),
        "latency_distribution": _write_svg(
            figures_dir / "latency_distribution.svg",
            _bar_svg(
                title="Mean latency by approach (ms)",
                values=[
                    (row["approach"], Decimal(str(row["mean_latency_ms"] or "0"))) for row in rates
                ],
                unit="ms",
            ),
        ),
        "class_wise_heatmap": _write_svg(
            figures_dir / "class_wise_heatmap.svg",
            _heatmap_svg(_subgroup_rows(records, case_map)),
        ),
        "cost_correctness_frontier": _write_svg(
            figures_dir / "cost_correctness_frontier.svg",
            _frontier_svg(rates),
        ),
    }

    report = {
        "schema_version": "1.0",
        "ok": True,
        "run_id": manifest.run_id,
        "record_count": len(records),
        "primary_record_count": len(primary_records),
        "stability_record_count": len(records) - len(primary_records),
        "validation": validation,
        "tables": table_refs,
        "figures": figure_refs,
    }
    report_path = run_dir / "analysis_report.json"
    report_path.write_text(canonical_json(report) + "\n", encoding="utf-8", newline="\n")
    _write_markdown_report(run_dir / "analysis_report.md", report)
    return report


def _dataset_composition_rows(cases: list[BenchmarkCase]) -> list[dict[str, Any]]:
    counter: Counter[tuple[str, str, str, str]] = Counter(
        (
            case.split.value,
            case.language.value,
            case.difficulty.value,
            case.intent.value,
        )
        for case in cases
    )
    return [
        {
            "split": split,
            "language": language,
            "difficulty": difficulty,
            "class": question_class,
            "n": count,
        }
        for (split, language, difficulty, question_class), count in sorted(counter.items())
    ]


def _approach_configuration_rows(manifest: Any) -> list[dict[str, Any]]:
    return [
        {
            "approach": approach.value,
            "model_provider": manifest.model.provider,
            "model_id": manifest.model.id,
            "prompt_id": manifest.prompts[approach].id,
            "prompt_sha256": manifest.prompts[approach].sha256,
            "repetitions": manifest.repetitions,
            "execution_design": manifest.execution_design.design_id
            if manifest.execution_design is not None
            else "uniform",
            "mode": manifest.mode.value,
        }
        for approach in manifest.approaches
    ]


def _failure_policy_rows(records: list[ResultRecord]) -> list[dict[str, Any]]:
    grouped: dict[Approach, list[ResultRecord]] = defaultdict(list)
    for record in records:
        grouped[record.approach].append(record)
    rows: list[dict[str, Any]] = []
    for approach, approach_records in sorted(grouped.items(), key=lambda item: item[0].value):
        rows.append(
            {
                "approach": approach.value,
                "n": len(approach_records),
                "answered": sum(record.outcome.value == "ANSWERED" for record in approach_records),
                "clarify": sum(record.outcome.value == "CLARIFY" for record in approach_records),
                "out_of_scope": sum(
                    record.outcome.value == "OUT_OF_SCOPE" for record in approach_records
                ),
                "error": sum(record.outcome.value == "ERROR" for record in approach_records),
                "unsafe_or_invalid": sum(
                    record.scores.unsafe_or_invalid for record in approach_records
                ),
                "false_refusal": sum(record.scores.false_refusal for record in approach_records),
            }
        )
    return rows


def _cost_latency_rows(records: list[ResultRecord]) -> list[dict[str, Any]]:
    return [
        {
            "approach": row["approach"],
            "n": row["n"],
            "mean_cost_usd": row["mean_cost_usd"],
            "median_cost_usd": row["median_cost_usd"],
            "mean_latency_ms": row["mean_latency_ms"],
            "median_latency_ms": row["median_latency_ms"],
        }
        for row in approach_metric_rates(records)
    ]


def _ambiguity_rows(
    records: list[ResultRecord],
    case_map: dict[str, BenchmarkCase],
) -> list[dict[str, Any]]:
    return _case_subset_rate_rows(
        records,
        case_map,
        predicate=lambda case: case.expected_policy.value == "CLARIFY",
        metric="clarification_decision_correct",
    )


def _multi_turn_rows(
    records: list[ResultRecord],
    case_map: dict[str, BenchmarkCase],
) -> list[dict[str, Any]]:
    return _case_subset_rate_rows(
        records,
        case_map,
        predicate=lambda case: case.split is DatasetSplit.MULTI_TURN,
        metric="sequence_state_correct",
    )


def _subgroup_rows(
    records: list[ResultRecord],
    case_map: dict[str, BenchmarkCase],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    subgroup_getters: tuple[tuple[str, Callable[[BenchmarkCase], str]], ...] = (
        ("language", _case_language),
        ("difficulty", _case_difficulty),
        ("class", _case_class),
    )
    for subgroup_type, getter in subgroup_getters:
        grouped: dict[tuple[str, str], list[ResultRecord]] = defaultdict(list)
        for record in records:
            case = case_map[record.case_id]
            grouped[(record.approach.value, getter(case))].append(record)
        for (approach, subgroup), subgroup_records in sorted(grouped.items()):
            values = [
                record.scores.answer_correct
                for record in subgroup_records
                if record.scores.answer_correct is not None
            ]
            rows.append(
                {
                    "approach": approach,
                    "subgroup_type": subgroup_type,
                    "subgroup": subgroup,
                    "n": len(values),
                    "answer_correct_rate": _rate(values),
                }
            )
    return rows


def _contrast_rows(records: list[ResultRecord]) -> list[dict[str, Any]]:
    rows = paired_binary_contrasts(records, metric="answer_correct")
    rows.extend(paired_binary_contrasts(records, metric="policy_correct"))
    rows.extend(paired_bootstrap_contrasts(records, metric="cost_usd", resamples=1000))
    rows.extend(paired_bootstrap_contrasts(records, metric="latency_ms", resamples=1000))
    return rows


def _case_subset_rate_rows(
    records: list[ResultRecord],
    case_map: dict[str, BenchmarkCase],
    *,
    predicate: Any,
    metric: str,
) -> list[dict[str, Any]]:
    grouped: dict[Approach, list[ResultRecord]] = defaultdict(list)
    for record in records:
        if predicate(case_map[record.case_id]):
            grouped[record.approach].append(record)
    rows: list[dict[str, Any]] = []
    for approach, approach_records in sorted(grouped.items(), key=lambda item: item[0].value):
        values = [
            getattr(record.scores, metric)
            for record in approach_records
            if getattr(record.scores, metric) is not None
        ]
        rows.append({"approach": approach.value, "n": len(values), f"{metric}_rate": _rate(values)})
    return rows


def _write_metric_dictionary(path: Path) -> dict[str, str]:
    write_metric_dictionary(path)
    return {"path": str(path.name), "sha256": f"sha256:{sha256_file(path)}"}


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> dict[str, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = sorted({key for row in rows for key in row}) or ["empty"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(row.get(key, "")) for key in fieldnames})
    return {"path": str(path.name), "sha256": f"sha256:{sha256_file(path)}"}


def _write_svg(path: Path, payload: str) -> dict[str, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8", newline="\n")
    return {"path": str(path.name), "sha256": f"sha256:{sha256_file(path)}"}


def _bar_svg(*, title: str, values: list[tuple[str, Decimal]], unit: str) -> str:
    width = 760
    height = 320
    margin = 56
    max_value = max([value for _, value in values] + [Decimal("1")])
    bar_width = 100
    gap = 48
    parts = [_svg_header(width, height, title)]
    for index, (label, value) in enumerate(values):
        x = margin + index * (bar_width + gap)
        bar_height = int(Decimal(height - 140) * value / max_value) if max_value else 0
        y = height - margin - bar_height
        parts.append(
            f'<rect x="{x}" y="{y}" width="{bar_width}" height="{bar_height}" fill="#4d4d4d"/>'
        )
        parts.append(
            f'<text x="{x + bar_width / 2}" y="{height - 30}" text-anchor="middle">{label}</text>'
        )
        parts.append(
            f'<text x="{x + bar_width / 2}" y="{max(24, y - 8)}" text-anchor="middle">'
            f"{_format_figure_value(value, unit)}</text>"
        )
    parts.append("</svg>\n")
    return "\n".join(parts)


def _heatmap_svg(rows: list[dict[str, Any]]) -> str:
    filtered = [row for row in rows if row["subgroup_type"] == "class"][:40]
    width = 900
    height = max(220, 36 + 22 * len(filtered))
    parts = [_svg_header(width, height, "Class-wise answer correctness heatmap source values")]
    y = 54
    for row in filtered:
        value = Decimal(str(row["answer_correct_rate"] or "0"))
        shade = 240 - int(160 * value)
        parts.append(
            f'<rect x="24" y="{y - 15}" width="840" height="18" '
            f'fill="rgb({shade},{shade},{shade})"/>'
        )
        parts.append(
            f'<text x="32" y="{y}">{row["approach"]} {row["subgroup"]} n={row["n"]} '
            f"rate={value}</text>"
        )
        y += 22
    parts.append("</svg>\n")
    return "\n".join(parts)


def _frontier_svg(rows: list[dict[str, Any]]) -> str:
    width = 760
    height = 360
    parts = [_svg_header(width, height, "Cost-correctness frontier")]
    parts.append('<line x1="60" y1="300" x2="700" y2="300" stroke="#222"/>')
    parts.append('<line x1="60" y1="300" x2="60" y2="60" stroke="#222"/>')
    max_cost = max(Decimal(str(row["mean_cost_usd"] or "0")) for row in rows) or Decimal("1")
    for row in rows:
        cost = Decimal(str(row["mean_cost_usd"] or "0"))
        correctness = Decimal(str(row["answer_correct_rate"] or "0"))
        x = 60 + int(620 * cost / max_cost)
        y = 300 - int(220 * correctness)
        parts.append(f'<circle cx="{x}" cy="{y}" r="7" fill="#111"/>')
        parts.append(f'<text x="{x + 10}" y="{y + 4}">{row["approach"]}</text>')
    parts.append("</svg>\n")
    return "\n".join(parts)


def _svg_header(width: int, height: int, title: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="{title}">'
        f"\n<title>{title}</title>\n"
        '<rect width="100%" height="100%" fill="white"/>'
        f'\n<text x="24" y="28" font-size="16" font-family="Arial">{title}</text>'
    )


def _write_markdown_report(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Experiment Analysis Report",
        "",
        f"- Run ID: `{report['run_id']}`",
        f"- Result records: {report['record_count']}",
        f"- Tables: {len(report['tables'])}",
        f"- Figures: {len(report['figures'])}",
        "",
        "All values are regenerated from validated result records.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def _csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


def _case_language(case: BenchmarkCase) -> str:
    return case.language.value


def _case_difficulty(case: BenchmarkCase) -> str:
    return case.difficulty.value


def _case_class(case: BenchmarkCase) -> str:
    return case.intent.value


def _rate(values: list[bool]) -> str:
    if not values:
        return ""
    return str((Decimal(sum(values)) / Decimal(len(values))).quantize(Decimal("0.000001")))


def _format_figure_value(value: Decimal, unit: str) -> str:
    if unit == "rate":
        return f"{(value * Decimal('100')).quantize(Decimal('0.1'))}%"
    if unit == "usd":
        return f"${value.quantize(Decimal('0.000001'))}"
    return str(value.quantize(Decimal("0.000001")))
