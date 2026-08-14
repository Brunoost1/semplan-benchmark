"""F3 benchmark artifact validation."""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

from semplan.benchmark.generator import _canonical_value
from semplan.contracts import (
    BenchmarkCase,
    ExpectedPolicy,
    GoldAnswer,
    ReviewStatus,
)
from semplan.data_generation.writer import canonical_json, sha256_file
from semplan.db import admin_database_url
from semplan.errors import ErrorCode, ProjectError
from semplan.executor.sql_guard import validate_select_sql


def validate_benchmark_dir(
    benchmark_dir: Path,
    *,
    require_approved: bool,
    execute_gold: bool = False,
    database_url: str | None = None,
    write_report: bool = True,
    allow_hidden: bool = False,
) -> dict[str, Any]:
    cases = load_benchmark_cases(benchmark_dir)
    findings: list[dict[str, str]] = []
    _validate_case_uniqueness(cases, findings)
    _validate_artifact_refs(benchmark_dir, cases, findings)
    _validate_lineage_splits(cases, findings)
    _validate_hidden_policy(cases, findings, allow_hidden=allow_hidden)
    answers = _validate_gold_answers(benchmark_dir, cases, require_approved, findings)
    if execute_gold:
        _validate_gold_execution(benchmark_dir, cases, answers, database_url, findings)

    report = _build_report(
        cases,
        answers,
        findings,
        require_approved,
        execute_gold,
        allow_hidden,
    )
    if write_report:
        _write_report(benchmark_dir, report)
    if report["summary"]["errors"] > 0:
        raise ProjectError(
            ErrorCode.CFG_INVALID,
            "Benchmark validation failed",
            detail={"errors": report["summary"]["errors"]},
        )
    return report


def load_benchmark_cases(benchmark_dir: Path) -> list[BenchmarkCase]:
    cases: list[BenchmarkCase] = []
    for path in sorted((benchmark_dir / "cases").glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line:
                cases.append(BenchmarkCase.model_validate_json(line))
    return cases


def _validate_case_uniqueness(cases: list[BenchmarkCase], findings: list[dict[str, str]]) -> None:
    ids = [case.case_id for case in cases]
    if len(ids) != len(set(ids)):
        findings.append(_finding("error", "duplicate_case_id", "cases"))

    utterances = [(case.language, case.utterance.casefold()) for case in cases]
    if len(utterances) != len(set(utterances)):
        findings.append(_finding("error", "duplicate_utterance", "cases"))


def _validate_artifact_refs(
    benchmark_dir: Path, cases: list[BenchmarkCase], findings: list[dict[str, str]]
) -> None:
    for case in cases:
        refs = [case.gold_answer_ref]
        if case.gold_semantic_plan_ref is not None:
            refs.append(case.gold_semantic_plan_ref)
        if case.gold_sql_ref is not None:
            refs.append(case.gold_sql_ref)
        for ref in refs:
            path = benchmark_dir / ref
            if not path.is_file():
                findings.append(_finding("error", "missing_artifact_ref", case.case_id))


def _validate_lineage_splits(cases: list[BenchmarkCase], findings: list[dict[str, str]]) -> None:
    splits_by_fingerprint: dict[str, set[str]] = defaultdict(set)
    families_by_fingerprint: dict[str, set[str]] = defaultdict(set)
    for case in cases:
        splits_by_fingerprint[case.semantic_fingerprint].add(case.split.value)
        families_by_fingerprint[case.semantic_fingerprint].add(case.template_family)
    for fingerprint, splits in splits_by_fingerprint.items():
        if len(splits) > 1:
            findings.append(_finding("error", "semantic_lineage_crosses_splits", fingerprint))
    for fingerprint, families in families_by_fingerprint.items():
        if len(families) > 1:
            findings.append(_finding("error", "fingerprint_reused_across_families", fingerprint))


def _validate_hidden_policy(
    cases: list[BenchmarkCase],
    findings: list[dict[str, str]],
    *,
    allow_hidden: bool,
) -> None:
    if not allow_hidden and any(case.split.value == "test_hidden" for case in cases):
        findings.append(_finding("error", "hidden_split_present", "cases"))


def _validate_gold_answers(
    benchmark_dir: Path,
    cases: list[BenchmarkCase],
    require_approved: bool,
    findings: list[dict[str, str]],
) -> dict[str, GoldAnswer]:
    answers: dict[str, GoldAnswer] = {}
    for case in cases:
        answer_path = benchmark_dir / case.gold_answer_ref
        if not answer_path.is_file():
            continue
        answer = GoldAnswer.model_validate_json(answer_path.read_text(encoding="utf-8"))
        answers[case.case_id] = answer
        if answer.case_id != case.case_id:
            findings.append(_finding("error", "gold_answer_case_mismatch", case.case_id))
        if answer.outcome != case.expected_policy:
            findings.append(_finding("error", "gold_answer_policy_mismatch", case.case_id))
        if case.expected_policy is ExpectedPolicy.ALLOW and not answer.rows:
            findings.append(_finding("warning", "empty_executable_gold_answer", case.case_id))
        if require_approved:
            if case.review.status is not ReviewStatus.APPROVED:
                findings.append(_finding("error", "case_review_not_approved", case.case_id))
            if answer.review.status is not ReviewStatus.APPROVED:
                findings.append(_finding("error", "gold_review_not_approved", case.case_id))
    return answers


def _validate_gold_execution(
    benchmark_dir: Path,
    cases: list[BenchmarkCase],
    answers: dict[str, GoldAnswer],
    database_url: str | None,
    findings: list[dict[str, str]],
) -> None:
    engine = create_engine(database_url or admin_database_url())
    try:
        with engine.connect() as connection:
            for case in cases:
                if case.expected_policy is not ExpectedPolicy.ALLOW:
                    continue
                if case.gold_sql_ref is None:
                    continue
                answer = answers.get(case.case_id)
                if answer is None:
                    continue
                sql = (benchmark_dir / case.gold_sql_ref).read_text(encoding="utf-8")
                guarded = validate_select_sql(sql)
                rows = [
                    {
                        key: _canonical_value(value, answer.units.get(key))
                        for key, value in row.items()
                    }
                    for row in connection.execute(text(guarded.normalized_sql)).mappings()
                ]
                if rows != answer.rows:
                    findings.append(_finding("error", "gold_execution_mismatch", case.case_id))
    finally:
        engine.dispose()


def _build_report(
    cases: list[BenchmarkCase],
    answers: dict[str, GoldAnswer],
    findings: list[dict[str, str]],
    require_approved: bool,
    execute_gold: bool,
    allow_hidden: bool,
) -> dict[str, Any]:
    severities = Counter(finding["severity"] for finding in findings)
    language_counts = Counter(case.language.value for case in cases)
    split_counts = Counter(case.split.value for case in cases)
    class_counts = Counter(case.intent.value for case in cases)
    difficulty_counts = Counter(case.difficulty.value for case in cases)
    review_counts = Counter(case.review.status.value for case in cases)
    review_counts.update(answer.review.status.value for answer in answers.values())
    return {
        "schema_version": "1.0",
        "status": "passed" if severities["error"] == 0 else "failed",
        "case_count": len(cases),
        "gold_answer_count": len(answers),
        "summary": {
            "errors": severities["error"],
            "warnings": severities["warning"],
            "infos": severities["info"],
        },
        "require_approved": require_approved,
        "execute_gold": execute_gold,
        "allow_hidden": allow_hidden,
        "language_counts": dict(language_counts),
        "split_counts": dict(split_counts),
        "class_counts": dict(class_counts),
        "difficulty_counts": dict(difficulty_counts),
        "review_counts": dict(review_counts),
        "findings": findings,
    }


def _write_report(benchmark_dir: Path, report: dict[str, Any]) -> None:
    (benchmark_dir / "validation_report.json").write_text(
        canonical_json(report) + "\n", encoding="utf-8", newline="\n"
    )
    lines = [
        "# Benchmark Validation Report",
        "",
        f"Status: {report['status']}",
        f"Cases: {report['case_count']}",
        f"Gold answers: {report['gold_answer_count']}",
        f"Errors: {report['summary']['errors']}",
        f"Warnings: {report['summary']['warnings']}",
        "",
        "## Review Counts",
        "",
    ]
    for status, count in sorted(report["review_counts"].items()):
        lines.append(f"- `{status}`: {count}")
    lines.append("")
    (benchmark_dir / "validation_report.md").write_text(
        "\n".join(lines), encoding="utf-8", newline="\n"
    )


def benchmark_file_hashes(benchmark_dir: Path) -> dict[str, str]:
    return {
        str(path.relative_to(benchmark_dir)): sha256_file(path)
        for path in sorted(benchmark_dir.rglob("*"))
        if path.is_file()
    }


def _finding(severity: str, code: str, location: str) -> dict[str, str]:
    return {"severity": severity, "code": code, "location": location}
