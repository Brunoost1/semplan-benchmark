"""Deterministic F7 benchmark freeze helpers."""

from __future__ import annotations

import hashlib
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from semplan.benchmark.validator import load_benchmark_cases, validate_benchmark_dir
from semplan.contracts import (
    BenchmarkCase,
    BenchmarkManifest,
    DatasetSplit,
    GoldAnswer,
    Locale,
    ReviewStatus,
    SemanticPlanEnvelope,
)
from semplan.data_generation.writer import canonical_json, sha256_file

F7_BENCHMARK_VERSION = "0.2.0-f7-primary"
F7_FREEZE_SEED = 20260806
F7_HIDDEN_FAMILY_COUNT = 4
F7_EXCLUDED_CASE_IDS = ("ADV-SMK-000001",)
F7_SPLIT_ORDER = (
    DatasetSplit.DEVELOPMENT,
    DatasetSplit.VALIDATION,
    DatasetSplit.TEST_PUBLIC,
    DatasetSplit.TEST_HIDDEN,
    DatasetSplit.MULTI_TURN,
    DatasetSplit.ADVERSARIAL,
)
F7_SCIENTIFIC_SPLITS = (
    DatasetSplit.TEST_PUBLIC,
    DatasetSplit.TEST_HIDDEN,
    DatasetSplit.MULTI_TURN,
    DatasetSplit.ADVERSARIAL,
)


def prepare_f7_primary_benchmark(
    source_dir: Path,
    output_dir: Path,
    *,
    overwrite: bool,
    seed: int = F7_FREEZE_SEED,
    hidden_family_count: int = F7_HIDDEN_FAMILY_COUNT,
    excluded_case_ids: tuple[str, ...] = F7_EXCLUDED_CASE_IDS,
) -> dict[str, Any]:
    """Create a frozen F7 benchmark from the approved F3 smoke benchmark.

    The source benchmark is left untouched. Hidden cases are selected by
    semantic family from development/validation candidates and renamed so the
    case ID encodes the target hidden split.
    """

    if output_dir.exists():
        if not overwrite:
            raise FileExistsError(f"Output directory already exists: {output_dir}")
        shutil.rmtree(output_dir)
    _make_dirs(output_dir)

    source_manifest_path = source_dir / "benchmark_manifest.json"
    source_manifest = BenchmarkManifest.model_validate_json(
        source_manifest_path.read_text(encoding="utf-8")
    )
    source_cases = load_benchmark_cases(source_dir)
    answers = _load_answers(source_dir)
    hidden_families = _select_hidden_families(
        source_cases,
        seed=seed,
        hidden_family_count=hidden_family_count,
        excluded_case_ids=set(excluded_case_ids),
    )
    transformed = _transform_cases_and_artifacts(
        source_dir=source_dir,
        output_dir=output_dir,
        source_cases=source_cases,
        answers=answers,
        hidden_families=hidden_families,
        excluded_case_ids=set(excluded_case_ids),
    )
    cases = transformed["cases"]
    gold_answers = transformed["answers"]

    _write_cases(output_dir, cases, source_manifest.dataset_version)
    leakage_report = _write_leakage_report(
        output_dir,
        cases,
        excluded_case_ids=excluded_case_ids,
        hidden_families=hidden_families,
    )
    _write_review_audit(
        output_dir,
        cases,
        gold_answers,
        source_manifest_hash=f"sha256:{sha256_file(source_manifest_path)}",
        lineage_map=transformed["lineage_map"],
        excluded_case_ids=excluded_case_ids,
        leakage_report=leakage_report,
    )
    _write_manifest(output_dir, cases, gold_answers, source_manifest)
    validation_report = validate_benchmark_dir(
        output_dir,
        require_approved=True,
        allow_hidden=True,
        write_report=True,
    )
    return {
        "ok": True,
        "benchmark_dir": str(output_dir),
        "benchmark_manifest_sha256": sha256_file(output_dir / "benchmark_manifest.json"),
        "case_count": len(cases),
        "scientific_case_count": sum(1 for case in cases if case.split in F7_SCIENTIFIC_SPLITS),
        "split_counts": {
            split.value: count for split, count in Counter(c.split for c in cases).items()
        },
        "hidden_families": hidden_families,
        "excluded_case_ids": list(excluded_case_ids),
        "validation_status": validation_report["status"],
        "leakage_status": leakage_report["status"],
    }


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


def _load_answers(source_dir: Path) -> dict[str, GoldAnswer]:
    return {
        path.stem: GoldAnswer.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted((source_dir / "gold/answers").glob("*.json"))
    }


def _select_hidden_families(
    source_cases: list[BenchmarkCase],
    *,
    seed: int,
    hidden_family_count: int,
    excluded_case_ids: set[str],
) -> list[str]:
    families: dict[str, list[BenchmarkCase]] = defaultdict(list)
    for case in source_cases:
        if case.case_id in excluded_case_ids:
            continue
        if case.split in {DatasetSplit.DEVELOPMENT, DatasetSplit.VALIDATION}:
            families[case.template_family].append(case)

    complete_families = {
        family: cases
        for family, cases in families.items()
        if {case.language for case in cases} == {Locale.EN_US, Locale.PT_BR}
    }
    if hidden_family_count > len(complete_families):
        raise ValueError(
            "Not enough complete bilingual development/validation families "
            f"for hidden split: requested {hidden_family_count}, "
            f"available {len(complete_families)}"
        )

    return sorted(
        complete_families,
        key=lambda family: hashlib.sha256(f"{seed}:{family}".encode()).hexdigest(),
    )[:hidden_family_count]


def _transform_cases_and_artifacts(
    *,
    source_dir: Path,
    output_dir: Path,
    source_cases: list[BenchmarkCase],
    answers: dict[str, GoldAnswer],
    hidden_families: list[str],
    excluded_case_ids: set[str],
) -> dict[str, Any]:
    hidden_family_set = set(hidden_families)
    output_cases: list[BenchmarkCase] = []
    output_answers: list[GoldAnswer] = []
    lineage_map: list[dict[str, str]] = []
    hidden_counter = 0

    for source_case in source_cases:
        if source_case.case_id in excluded_case_ids:
            continue

        if source_case.template_family in hidden_family_set:
            hidden_counter += 1
            target_case_id = f"TST-HID-SMK-{hidden_counter:06d}"
            target_case = source_case.model_copy(
                update={
                    "case_id": target_case_id,
                    "split": DatasetSplit.TEST_HIDDEN,
                    "gold_semantic_plan_ref": _target_ref(
                        source_case.gold_semantic_plan_ref,
                        target_case_id,
                    ),
                    "gold_sql_ref": _target_ref(source_case.gold_sql_ref, target_case_id),
                    "gold_answer_ref": f"gold/answers/{target_case_id}.json",
                }
            )
        else:
            target_case_id = source_case.case_id
            target_case = source_case

        source_answer = answers[source_case.case_id]
        target_answer = _copy_case_artifacts(
            source_dir=source_dir,
            output_dir=output_dir,
            source_case=source_case,
            target_case=target_case,
            source_answer=source_answer,
        )
        output_cases.append(target_case)
        output_answers.append(target_answer)
        lineage_map.append(
            {
                "source_case_id": source_case.case_id,
                "f7_case_id": target_case.case_id,
                "source_split": source_case.split.value,
                "f7_split": target_case.split.value,
                "language": source_case.language.value,
                "template_family": source_case.template_family,
                "semantic_fingerprint": source_case.semantic_fingerprint,
            }
        )

    _write_json(
        output_dir / "review/f7_hidden_lineage_map.json",
        {
            "schema_version": "1.0",
            "hidden_family_count": len(hidden_families),
            "hidden_families": hidden_families,
            "lineage": lineage_map,
        },
    )
    _write_json(
        output_dir / "review/f7_scientific_exclusions.json",
        {
            "schema_version": "1.0",
            "excluded": [
                {
                    "case_id": case_id,
                    "reason": "owner-authorized paid smoke case; excluded from scientific run",
                }
                for case_id in sorted(excluded_case_ids)
            ],
        },
    )
    return {"cases": output_cases, "answers": output_answers, "lineage_map": lineage_map}


def _target_ref(source_ref: str | None, target_case_id: str) -> str | None:
    if source_ref is None:
        return None
    if source_ref.startswith("gold/plans/"):
        return f"gold/plans/{target_case_id}.json"
    if source_ref.startswith("gold/sql/"):
        return f"gold/sql/{target_case_id}.sql"
    raise ValueError(f"Unsupported gold reference: {source_ref}")


def _copy_case_artifacts(
    *,
    source_dir: Path,
    output_dir: Path,
    source_case: BenchmarkCase,
    target_case: BenchmarkCase,
    source_answer: GoldAnswer,
) -> GoldAnswer:
    plan_hash = source_answer.plan_hash
    if (
        source_case.gold_semantic_plan_ref is not None
        and target_case.gold_semantic_plan_ref is not None
    ):
        source_plan = SemanticPlanEnvelope.model_validate_json(
            (source_dir / source_case.gold_semantic_plan_ref).read_text(encoding="utf-8")
        )
        target_plan = source_plan.model_copy(update={"plan_id": f"gold-plan:{target_case.case_id}"})
        plan_payload = target_plan.model_dump(mode="json")
        _write_json(output_dir / target_case.gold_semantic_plan_ref, plan_payload)
        plan_hash = f"sha256:{_sha256_text(canonical_json(plan_payload))}"

    if source_case.gold_sql_ref is not None and target_case.gold_sql_ref is not None:
        source_sql = source_dir / source_case.gold_sql_ref
        target_sql = output_dir / target_case.gold_sql_ref
        target_sql.write_text(
            source_sql.read_text(encoding="utf-8"), encoding="utf-8", newline="\n"
        )

    target_answer = source_answer.model_copy(
        update={"case_id": target_case.case_id, "plan_hash": plan_hash}
    )
    _write_json(
        output_dir / target_case.gold_answer_ref,
        target_answer.model_dump(mode="json"),
    )
    return target_answer


def _write_cases(output_dir: Path, cases: list[BenchmarkCase], dataset_version: str) -> None:
    by_split: dict[DatasetSplit, list[BenchmarkCase]] = {split: [] for split in F7_SPLIT_ORDER}
    for case in cases:
        by_split[case.split].append(case)
    for split in F7_SPLIT_ORDER:
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
            "dataset_version": dataset_version,
            "case_count": len(split_cases),
            "case_file": f"cases/{split.value}.jsonl",
            "case_file_sha256": sha256_file(path),
            "hidden_sealed": split is DatasetSplit.TEST_HIDDEN,
        }
        _write_json(output_dir / "split_manifests" / f"{split.value}.json", split_manifest)


def _write_leakage_report(
    output_dir: Path,
    cases: list[BenchmarkCase],
    *,
    excluded_case_ids: tuple[str, ...],
    hidden_families: list[str],
) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    _add_duplicate_findings(cases, findings)
    _add_cross_split_findings(cases, findings, "semantic_fingerprint")
    _add_cross_split_findings(cases, findings, "template_family")
    case_ids = {case.case_id for case in cases}
    for excluded_case_id in excluded_case_ids:
        if excluded_case_id in case_ids:
            findings.append(
                {
                    "severity": "error",
                    "code": "excluded_smoke_case_present",
                    "location": excluded_case_id,
                }
            )

    errors = sum(1 for finding in findings if finding["severity"] == "error")
    report = {
        "schema_version": "1.0",
        "status": "passed" if errors == 0 else "failed",
        "case_count": len(cases),
        "hidden_families": hidden_families,
        "excluded_case_ids": list(excluded_case_ids),
        "findings": findings,
    }
    _write_json(output_dir / "review/f7_leakage_report.json", report)
    lines = [
        "# F7 Leakage Report",
        "",
        f"Status: {report['status']}",
        f"Cases: {len(cases)}",
        f"Findings: {len(findings)}",
        "",
        "## Hidden Families",
        "",
    ]
    lines.extend(f"- `{family}`" for family in hidden_families)
    lines.append("")
    if findings:
        lines.append("## Findings")
        lines.append("")
        for finding in findings:
            lines.append(
                f"- `{finding['severity']}` `{finding['code']}` at `{finding['location']}`"
            )
        lines.append("")
    (output_dir / "review/f7_leakage_report.md").write_text(
        "\n".join(lines), encoding="utf-8", newline="\n"
    )
    return report


def _add_duplicate_findings(cases: list[BenchmarkCase], findings: list[dict[str, str]]) -> None:
    seen_ids: set[str] = set()
    seen_utterances: set[tuple[str, str]] = set()
    for case in cases:
        if case.case_id in seen_ids:
            findings.append(
                {"severity": "error", "code": "duplicate_case_id", "location": case.case_id}
            )
        seen_ids.add(case.case_id)
        utterance_key = (case.language.value, case.utterance.casefold())
        if utterance_key in seen_utterances:
            findings.append(
                {
                    "severity": "error",
                    "code": "duplicate_utterance",
                    "location": case.case_id,
                }
            )
        seen_utterances.add(utterance_key)


def _add_cross_split_findings(
    cases: list[BenchmarkCase],
    findings: list[dict[str, str]],
    field_name: str,
) -> None:
    splits_by_value: dict[str, set[str]] = defaultdict(set)
    for case in cases:
        value = getattr(case, field_name)
        splits_by_value[value].add(case.split.value)
    for value, splits in splits_by_value.items():
        if len(splits) > 1:
            findings.append(
                {
                    "severity": "error",
                    "code": f"{field_name}_crosses_splits",
                    "location": value,
                }
            )


def _write_review_audit(
    output_dir: Path,
    cases: list[BenchmarkCase],
    answers: list[GoldAnswer],
    *,
    source_manifest_hash: str,
    lineage_map: list[dict[str, str]],
    excluded_case_ids: tuple[str, ...],
    leakage_report: dict[str, Any],
) -> None:
    case_review_counts = Counter(case.review.status for case in cases)
    answer_review_counts = Counter(answer.review.status for answer in answers)
    all_approved = set(case_review_counts) == {ReviewStatus.APPROVED} and set(
        answer_review_counts
    ) == {ReviewStatus.APPROVED}
    hidden_cases = [entry for entry in lineage_map if entry["f7_split"] == "test_hidden"]
    payload = {
        "schema_version": "1.0",
        "status": "complete" if all_approved else "pending_author_review",
        "source_benchmark_manifest_sha256": source_manifest_hash,
        "case_review_counts": {status.value: count for status, count in case_review_counts.items()},
        "gold_review_counts": {
            status.value: count for status, count in answer_review_counts.items()
        },
        "hidden_case_count": len(hidden_cases),
        "excluded_case_ids": list(excluded_case_ids),
        "leakage_status": leakage_report["status"],
    }
    _write_json(output_dir / "review/f7_author_review_audit.json", payload)
    lines = [
        "# F7 Author Review Audit",
        "",
        f"Status: {payload['status']}",
        f"Source benchmark manifest: `{source_manifest_hash}`",
        f"Included cases: {len(cases)}",
        f"Hidden cases: {len(hidden_cases)}",
        f"Excluded smoke cases: {', '.join(f'`{case_id}`' for case_id in excluded_case_ids)}",
        "",
        "## Review Counts",
        "",
    ]
    lines.extend(
        f"- Cases `{status.value}`: {count}" for status, count in sorted(case_review_counts.items())
    )
    lines.extend(
        f"- Gold `{status.value}`: {count}"
        for status, count in sorted(answer_review_counts.items())
    )
    lines.extend(
        [
            "",
            "## Hidden Case Lineage",
            "",
        ]
    )
    for entry in hidden_cases:
        lines.append(
            f"- `{entry['source_case_id']}` -> `{entry['f7_case_id']}` "
            f"({entry['template_family']}, {entry['language']})"
        )
    lines.append("")
    (output_dir / "review/f7_author_review_audit.md").write_text(
        "\n".join(lines), encoding="utf-8", newline="\n"
    )


def _write_manifest(
    output_dir: Path,
    cases: list[BenchmarkCase],
    answers: list[GoldAnswer],
    source_manifest: BenchmarkManifest,
) -> None:
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
        benchmark_version=F7_BENCHMARK_VERSION,
        dataset_version=source_manifest.dataset_version,
        dataset_manifest_hash=source_manifest.dataset_manifest_hash,
        state="frozen",
        case_count=len(cases),
        split_counts=dict(split_counts),
        language_counts=dict(language_counts),
        file_hashes=file_hashes,
        hidden_included=True,
        review_summary=dict(review_counts),
    )
    _write_json(output_dir / "benchmark_manifest.json", manifest.model_dump(mode="json"))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json(payload) + "\n", encoding="utf-8", newline="\n")
