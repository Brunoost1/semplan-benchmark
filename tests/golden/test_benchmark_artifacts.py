from __future__ import annotations

from pathlib import Path

from semplan.benchmark import load_benchmark_cases, validate_benchmark_dir

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_DIR = PROJECT_ROOT / "data/benchmark/f3_smoke"


def test_f3_smoke_benchmark_artifacts_validate_structurally() -> None:
    report = validate_benchmark_dir(BENCHMARK_DIR, require_approved=False, write_report=False)

    assert report["status"] == "passed"
    assert report["case_count"] == 50
    assert report["gold_answer_count"] == 50
    assert report["language_counts"] == {"en-US": 25, "pt-BR": 25}
    assert report["split_counts"].get("test_hidden", 0) == 0


def test_f3_smoke_cases_keep_lineage_within_split() -> None:
    cases = load_benchmark_cases(BENCHMARK_DIR)
    splits_by_fingerprint: dict[str, set[str]] = {}
    for case in cases:
        splits_by_fingerprint.setdefault(case.semantic_fingerprint, set()).add(case.split.value)

    assert cases
    assert all(len(splits) == 1 for splits in splits_by_fingerprint.values())


def test_f3_approval_gate_passes_for_author_reviewed_fixture() -> None:
    report = validate_benchmark_dir(BENCHMARK_DIR, require_approved=True, write_report=False)

    assert report["status"] == "passed"
    assert report["review_counts"] == {"approved": 100}
