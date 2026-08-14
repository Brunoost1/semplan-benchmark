"""F3 smoke benchmark artifact generation."""

from __future__ import annotations

import hashlib
import json
import shutil
from collections import Counter
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

from semplan.benchmark.templates import TemplateSpec, f3_smoke_templates
from semplan.catalog import load_catalog
from semplan.contracts import (
    BenchmarkCase,
    BenchmarkContext,
    BenchmarkManifest,
    BenchmarkReview,
    ClarificationTarget,
    DatasetSplit,
    DimensionSpec,
    Direction,
    ExecutionOperator,
    ExecutionPolicy,
    ExecutionSpec,
    ExpectedPolicy,
    GoldAnswer,
    Locale,
    MetricSpec,
    OrderingSpec,
    PredicateGroup,
    PredicateLeaf,
    ProvenanceSpec,
    ReviewStatus,
    ScalarValue,
    SemanticPlanEnvelope,
    SortSpec,
    TimeContext,
    ToleranceSpec,
)
from semplan.data_generation.loader import load_dataset
from semplan.data_generation.writer import canonical_json, sha256_file
from semplan.db import admin_database_url
from semplan.executor.sql_guard import validate_select_sql

BENCHMARK_VERSION = "0.1.0"
REFERENCE_DATE = date(2026, 8, 1)
EXECUTION_TIMESTAMP = datetime(2026, 8, 6, 0, 0, tzinfo=UTC)
SPLIT_CODE = {
    DatasetSplit.DEVELOPMENT: "DEV",
    DatasetSplit.VALIDATION: "VAL",
    DatasetSplit.TEST_PUBLIC: "TST-PUB",
    DatasetSplit.MULTI_TURN: "MT",
    DatasetSplit.ADVERSARIAL: "ADV",
}
SPLIT_ORDER = (
    DatasetSplit.DEVELOPMENT,
    DatasetSplit.VALIDATION,
    DatasetSplit.TEST_PUBLIC,
    DatasetSplit.MULTI_TURN,
    DatasetSplit.ADVERSARIAL,
)


def generate_smoke_benchmark(
    output_dir: Path,
    dataset_dir: Path,
    *,
    overwrite: bool,
    database_url: str | None = None,
) -> BenchmarkManifest:
    """Generate the F3 50-case smoke benchmark against a loaded PostgreSQL dataset."""

    if output_dir.exists():
        if not overwrite:
            raise FileExistsError(f"Output directory already exists: {output_dir}")
        shutil.rmtree(output_dir)
    _make_dirs(output_dir)

    dataset_manifest = json.loads((dataset_dir / "dataset_manifest.json").read_text())
    dataset_manifest_hash = f"sha256:{sha256_file(dataset_dir / 'dataset_manifest.json')}"
    load_dataset(dataset_dir, database_url)

    catalog = load_catalog(Path("catalog"))
    catalog_hash = f"sha256:{catalog.sha256()}"
    metrics_by_id = catalog.metrics
    cases: list[BenchmarkCase] = []
    answers: list[GoldAnswer] = []
    split_counters: Counter[DatasetSplit] = Counter()

    engine = create_engine(database_url or admin_database_url())
    try:
        with engine.connect() as connection:
            for spec in f3_smoke_templates():
                for language in (Locale.EN_US, Locale.PT_BR):
                    split_counters[spec.split] += 1
                    case_id = f"{SPLIT_CODE[spec.split]}-SMK-{split_counters[spec.split]:06d}"
                    case = _build_case(spec, language, case_id)
                    cases.append(case)
                    answer = _write_gold_artifacts(
                        output_dir,
                        spec,
                        case,
                        dataset_manifest["dataset_version"],
                        dataset_manifest_hash,
                        catalog_hash,
                        metrics_by_id,
                        connection,
                    )
                    answers.append(answer)
    finally:
        engine.dispose()

    _write_cases(output_dir, cases)
    _write_review_queue(output_dir, cases)
    manifest = _write_manifest(output_dir, cases, answers, dataset_manifest, dataset_manifest_hash)
    return manifest


def _make_dirs(output_dir: Path) -> None:
    for relative in [
        "cases",
        "gold/plans",
        "gold/sql",
        "gold/answers",
        "review",
        "split_manifests",
    ]:
        (output_dir / relative).mkdir(parents=True, exist_ok=True)


def _build_case(spec: TemplateSpec, language: Locale, case_id: str) -> BenchmarkCase:
    fingerprint = _semantic_fingerprint(spec)
    answer_ref = f"gold/answers/{case_id}.json"
    plan_ref = (
        f"gold/plans/{case_id}.json" if spec.expected_policy is ExpectedPolicy.ALLOW else None
    )
    sql_ref = f"gold/sql/{case_id}.sql" if spec.expected_policy is ExpectedPolicy.ALLOW else None
    clarification = None
    if spec.expected_policy is ExpectedPolicy.CLARIFY:
        clarification = ClarificationTarget(
            question_intent=spec.clarification_intent or "Clarify ambiguous analytical intent.",
            acceptable_resolution_choices=list(spec.resolution_choices),
        )
    return BenchmarkCase(
        schema_version="1.0",
        case_id=case_id,
        split=spec.split,
        language=language,
        utterance=spec.utterances[language],
        context=BenchmarkContext(reference_date=REFERENCE_DATE, timezone="UTC"),
        expected_operation=spec.expected_operation,
        intent=spec.question_class,
        difficulty=spec.difficulty,
        requires_clarification=spec.expected_policy is ExpectedPolicy.CLARIFY,
        gold_semantic_plan_ref=plan_ref,
        gold_sql_ref=sql_ref,
        gold_answer_ref=answer_ref,
        expected_policy=spec.expected_policy,
        tags=list(spec.tags),
        template_family=spec.family,
        semantic_fingerprint=fingerprint,
        clarification=clarification,
        review=_pending_review(),
    )


def _write_gold_artifacts(
    output_dir: Path,
    spec: TemplateSpec,
    case: BenchmarkCase,
    dataset_version: str,
    dataset_manifest_hash: str,
    catalog_hash: str,
    metrics_by_id: dict[str, Any],
    connection: Any,
) -> GoldAnswer:
    sql_hash = None
    plan_hash = None
    if spec.sql is not None:
        guarded = validate_select_sql(spec.sql)
        sql_path = output_dir / f"gold/sql/{case.case_id}.sql"
        sql_path.write_text(guarded.normalized_sql + "\n", encoding="utf-8", newline="\n")
        sql_hash = f"sha256:{_sha256_text(guarded.normalized_sql)}"

        plan = _build_plan(spec, case, catalog_hash, metrics_by_id)
        plan_payload = plan.model_dump(mode="json")
        plan_path = output_dir / f"gold/plans/{case.case_id}.json"
        _write_json(plan_path, plan_payload)
        plan_hash = f"sha256:{_sha256_text(canonical_json(plan_payload))}"
        rows = _execute_rows(connection, guarded.normalized_sql, spec.units)
    else:
        rows = []

    answer = GoldAnswer(
        schema_version="1.0",
        case_id=case.case_id,
        outcome=spec.expected_policy,
        dataset_version=dataset_version,
        dataset_manifest_hash=dataset_manifest_hash,
        query_hash=sql_hash,
        plan_hash=plan_hash,
        execution_timestamp_utc=EXECUTION_TIMESTAMP,
        rows=rows,
        units=spec.units,
        ordering=OrderingSpec(
            ordered=bool(spec.ordered_fields),
            fields=list(spec.ordered_fields),
            tie_policy="deterministic secondary sort declared in gold SQL",
        ),
        tolerances=_tolerances(spec.units),
        assumptions=list(spec.assumptions),
        review=_pending_review(),
    )
    _write_json(output_dir / f"gold/answers/{case.case_id}.json", answer.model_dump(mode="json"))
    return answer


def _build_plan(
    spec: TemplateSpec,
    case: BenchmarkCase,
    catalog_hash: str,
    metrics_by_id: dict[str, Any],
) -> SemanticPlanEnvelope:
    leaves = [
        PredicateLeaf(
            type="predicate",
            field=str(filter_spec["field"]),
            operator=filter_spec["operator"],
            value=filter_spec["value"],
        )
        for filter_spec in spec.filters
    ]
    operator = (
        ExecutionOperator.RANK
        if spec.question_class.value == "ranking"
        else ExecutionOperator.AGGREGATE
    )
    request_hash = _semantic_fingerprint(spec)
    return SemanticPlanEnvelope(
        schema_version="1.0",
        plan_id=f"gold-plan:{case.case_id}",
        operation=spec.expected_operation,
        metric_specs=[
            MetricSpec(id=metric_id, aggregation=metrics_by_id[metric_id].aggregation)
            for metric_id in spec.metrics
        ],
        dimension_specs=[DimensionSpec(id=dimension_id) for dimension_id in spec.dimensions],
        predicate_tree=PredicateGroup(
            type="AND",
            children=[leaf for leaf in leaves],
        ),
        time_context=TimeContext(
            reference_date=REFERENCE_DATE,
            timezone="UTC",
            grain=spec.time_grain,
        ),
        sort_specs=[
            SortSpec(field=sort_spec["field"], direction=Direction(sort_spec["direction"]))
            for sort_spec in spec.sort
        ],
        limit=spec.limit,
        execution=ExecutionSpec(operator=operator, policy=ExecutionPolicy.READ_ONLY, max_rows=1000),
        provenance=ProvenanceSpec(
            request_hash=request_hash,
            normalizer_version="gold-builder-0.1.0",
            catalog_hash=catalog_hash,
            defaults=[],
        ),
    )


def _execute_rows(connection: Any, sql: str, units: dict[str, str]) -> list[dict[str, ScalarValue]]:
    result = connection.execute(text(sql))
    return [
        {key: _canonical_value(value, units.get(key)) for key, value in row.items()}
        for row in result.mappings()
    ]


def _canonical_value(value: object, unit: str | None) -> ScalarValue:
    if isinstance(value, Decimal):
        if unit == "usd":
            return f"{value.quantize(Decimal('0.01')):.2f}"
        if unit == "ratio":
            return f"{value.quantize(Decimal('0.000001')):.6f}"
        if unit == "count":
            return int(value)
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    raise TypeError(f"Unsupported canonical value type: {type(value).__name__}")


def _tolerances(units: dict[str, str]) -> dict[str, ToleranceSpec]:
    tolerances: dict[str, ToleranceSpec] = {}
    for field, unit in units.items():
        if unit == "usd":
            tolerances[field] = ToleranceSpec(absolute=Decimal("0.01"), relative=Decimal("0"))
        elif unit == "ratio":
            tolerances[field] = ToleranceSpec(absolute=Decimal("0.000001"), relative=Decimal("0"))
        else:
            tolerances[field] = ToleranceSpec(absolute=Decimal("0"), relative=Decimal("0"))
    return tolerances


def _write_cases(output_dir: Path, cases: list[BenchmarkCase]) -> None:
    by_split: dict[DatasetSplit, list[BenchmarkCase]] = {split: [] for split in SPLIT_ORDER}
    for case in cases:
        by_split[case.split].append(case)
    for split in SPLIT_ORDER:
        split_cases = by_split[split]
        if not split_cases:
            continue
        path = output_dir / "cases" / f"{split.value}.jsonl"
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            for case in split_cases:
                handle.write(canonical_json(case.model_dump(mode="json")) + "\n")
        split_manifest = {
            "schema_version": "1.0",
            "split": split.value,
            "case_count": len(split_cases),
            "case_file": f"cases/{split.value}.jsonl",
            "case_file_sha256": sha256_file(path),
        }
        _write_json(output_dir / "split_manifests" / f"{split.value}.json", split_manifest)


def _write_review_queue(output_dir: Path, cases: list[BenchmarkCase]) -> None:
    lines = [
        "# F3 Smoke Benchmark Review Queue",
        "",
        "Status: pending author review",
        "",
        "Reviewer checklist: utterance intent, catalog IDs, date interpretation, filters, "
        "grouping, aggregation, sorting, limits, policy outcome, database result, bilingual "
        "equivalence, and split leakage.",
        "",
    ]
    for case in cases:
        lines.append(
            f"- `{case.case_id}` `{case.language}` `{case.intent}` "
            f"`{case.template_family}`: {case.utterance}"
        )
    lines.append("")
    (output_dir / "review/review_queue.md").write_text(
        "\n".join(lines), encoding="utf-8", newline="\n"
    )


def _write_manifest(
    output_dir: Path,
    cases: list[BenchmarkCase],
    answers: list[GoldAnswer],
    dataset_manifest: dict[str, Any],
    dataset_manifest_hash: str,
) -> BenchmarkManifest:
    file_hashes = {
        str(path.relative_to(output_dir)): sha256_file(path)
        for path in sorted(output_dir.rglob("*"))
        if path.is_file()
        and path.name
        not in {"benchmark_manifest.json", "validation_report.json", "validation_report.md"}
    }
    split_counts = Counter(case.split for case in cases)
    language_counts = Counter(case.language for case in cases)
    review_counts = Counter(case.review.status for case in cases)
    review_counts.update(answer.review.status for answer in answers)
    manifest = BenchmarkManifest(
        schema_version="1.0",
        benchmark_version=BENCHMARK_VERSION,
        dataset_version=str(dataset_manifest["dataset_version"]),
        dataset_manifest_hash=dataset_manifest_hash,
        state="validated",
        case_count=len(cases),
        split_counts=dict(split_counts),
        language_counts=dict(language_counts),
        file_hashes=file_hashes,
        hidden_included=False,
        review_summary=dict(review_counts),
    )
    _write_json(output_dir / "benchmark_manifest.json", manifest.model_dump(mode="json"))
    return manifest


def _pending_review() -> BenchmarkReview:
    return BenchmarkReview(
        status=ReviewStatus.PENDING_AUTHOR_REVIEW,
        notes=["Generated for F3 automated review queue; explicit owner approval is pending."],
    )


def _semantic_fingerprint(spec: TemplateSpec) -> str:
    payload = {
        "family": spec.family,
        "policy": spec.expected_policy.value,
        "metrics": spec.metrics,
        "dimensions": spec.dimensions,
        "filters": [_json_safe_filter(filter_spec) for filter_spec in spec.filters],
        "sql": spec.sql,
    }
    return f"sha256:{_sha256_text(canonical_json(payload))}"


def _json_safe_filter(filter_spec: dict[str, Any]) -> dict[str, Any]:
    value = filter_spec["value"]
    operator = filter_spec["operator"]
    if hasattr(operator, "value"):
        operator = operator.value
    return {"field": filter_spec["field"], "operator": operator, "value": value}


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.write_text(canonical_json(payload) + "\n", encoding="utf-8", newline="\n")
