"""Human-review metadata helpers for benchmark artifacts."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import cast

from semplan.benchmark.validator import load_benchmark_cases, validate_benchmark_dir
from semplan.contracts import BenchmarkCase, BenchmarkReview, GoldAnswer, ReviewStatus
from semplan.data_generation.writer import canonical_json, sha256_file


def approve_benchmark_reviews(
    benchmark_dir: Path,
    *,
    reviewer: str,
    reviewed_at: datetime,
    note: str,
    allow_hidden: bool = False,
) -> dict[str, object]:
    """Apply explicit owner approval metadata to cases and gold answers.

    This function deliberately requires caller-supplied reviewer metadata. It is
    tooling for recording approval after review, not a substitute for review.
    """

    if not reviewer.strip():
        raise ValueError("reviewer is required")
    if reviewed_at.tzinfo is None:
        raise ValueError("reviewed_at must be timezone-aware")
    if not note.strip():
        raise ValueError("approval note is required")

    validate_benchmark_dir(
        benchmark_dir,
        require_approved=False,
        allow_hidden=allow_hidden,
        write_report=False,
    )
    review = BenchmarkReview(
        status=ReviewStatus.APPROVED,
        reviewer=reviewer,
        reviewed_at=reviewed_at,
        notes=[note],
    )
    _rewrite_case_files(benchmark_dir, review)
    _rewrite_gold_answers(benchmark_dir, review)
    manifest = refresh_benchmark_manifest(benchmark_dir)
    report = validate_benchmark_dir(
        benchmark_dir,
        require_approved=True,
        allow_hidden=allow_hidden,
        write_report=True,
    )
    return {
        "case_count": report["case_count"],
        "review_counts": report["review_counts"],
        "benchmark_manifest_sha256": sha256_file(benchmark_dir / "benchmark_manifest.json"),
        "file_count": len(cast(dict[str, str], manifest["file_hashes"])),
    }


def refresh_benchmark_manifest(benchmark_dir: Path) -> dict[str, object]:
    manifest_path = benchmark_dir / "benchmark_manifest.json"
    manifest: dict[str, object] = json.loads(manifest_path.read_text(encoding="utf-8"))
    cases = load_benchmark_cases(benchmark_dir)
    answers = [
        GoldAnswer.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted((benchmark_dir / "gold/answers").glob("*.json"))
    ]
    review_counts = Counter(case.review.status.value for case in cases)
    review_counts.update(answer.review.status.value for answer in answers)
    manifest["case_count"] = len(cases)
    manifest["split_counts"] = dict(Counter(case.split.value for case in cases))
    manifest["language_counts"] = dict(Counter(case.language.value for case in cases))
    manifest["review_summary"] = dict(review_counts)
    manifest["file_hashes"] = {
        str(path.relative_to(benchmark_dir)): sha256_file(path)
        for path in sorted(benchmark_dir.rglob("*"))
        if path.is_file()
        and path.name
        not in {"benchmark_manifest.json", "validation_report.json", "validation_report.md"}
    }
    manifest_path.write_text(canonical_json(manifest) + "\n", encoding="utf-8", newline="\n")
    return manifest


def _rewrite_case_files(benchmark_dir: Path, review: BenchmarkReview) -> None:
    for path in sorted((benchmark_dir / "cases").glob("*.jsonl")):
        lines: list[str] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line:
                continue
            case = load_case_json(line)
            approved_case = case.model_copy(update={"review": review})
            lines.append(canonical_json(approved_case.model_dump(mode="json")))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _rewrite_gold_answers(benchmark_dir: Path, review: BenchmarkReview) -> None:
    for path in sorted((benchmark_dir / "gold/answers").glob("*.json")):
        answer = GoldAnswer.model_validate_json(path.read_text(encoding="utf-8"))
        approved_answer = answer.model_copy(update={"review": review})
        path.write_text(
            canonical_json(approved_answer.model_dump(mode="json")) + "\n",
            encoding="utf-8",
            newline="\n",
        )


def load_case_json(line: str) -> BenchmarkCase:
    return BenchmarkCase.model_validate_json(line)
