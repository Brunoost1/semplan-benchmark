from __future__ import annotations

import json
from collections import Counter
from contextlib import AbstractContextManager
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import TracebackType

import pytest

from semplan.benchmark import validate_benchmark_dir
from semplan.benchmark.freeze import prepare_f7_primary_benchmark
from semplan.benchmark.generator import (
    _canonical_value,
    generate_smoke_benchmark,
)
from semplan.benchmark.release_scale import (
    RELEASE_SCALE_BENCHMARK_VERSION,
    build_release_specs,
    generate_release_scale_benchmark,
    release_target_matrix,
    validate_release_scale_benchmark,
)
from semplan.benchmark.review import approve_benchmark_reviews, load_case_json
from semplan.benchmark.validator import benchmark_file_hashes
from semplan.cli.main import main as cli_main
from semplan.contracts import DatasetSplit, Locale, QuestionClass, ReviewStatus
from semplan.data_generation.writer import canonical_json
from semplan.errors import ProjectError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROJECT_BENCHMARK_DIR = PROJECT_ROOT / "data/benchmark/f3_smoke"


class FakeResult:
    def mappings(self) -> list[dict[str, object]]:
        return [{"stub_count": Decimal("12")}]


class FakeConnection(AbstractContextManager["FakeConnection"]):
    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def execute(self, _statement: object) -> FakeResult:
        return FakeResult()


class FakeEngine:
    def connect(self) -> FakeConnection:
        return FakeConnection()

    def dispose(self) -> None:
        return None


def test_generate_smoke_benchmark_writes_valid_artifacts_without_real_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset_dir = tmp_path / "dataset"
    output_dir = tmp_path / "benchmark"
    dataset_dir.mkdir()
    (dataset_dir / "dataset_manifest.json").write_text(
        json.dumps({"dataset_version": "0.1.0"}) + "\n", encoding="utf-8"
    )

    monkeypatch.setattr("semplan.benchmark.generator.load_dataset", lambda *_args: {})
    monkeypatch.setattr("semplan.benchmark.generator.create_engine", lambda *_args: FakeEngine())
    monkeypatch.setattr("semplan.benchmark.validator.create_engine", lambda *_args: FakeEngine())

    manifest = generate_smoke_benchmark(
        output_dir,
        dataset_dir,
        overwrite=False,
        database_url="postgresql+psycopg://fake",
    )
    report = validate_benchmark_dir(
        output_dir,
        require_approved=False,
        execute_gold=True,
        database_url="postgresql+psycopg://fake",
        write_report=False,
    )

    assert manifest.case_count == 50
    assert report["status"] == "passed"
    assert report["gold_answer_count"] == 50

    written_report = validate_benchmark_dir(
        output_dir,
        require_approved=False,
        execute_gold=False,
        write_report=True,
    )
    hashes = benchmark_file_hashes(output_dir)

    assert written_report["status"] == "passed"
    assert "validation_report.json" in hashes
    assert (output_dir / "validation_report.md").is_file()

    (output_dir / "gold/answers/DEV-SMK-000001.json").unlink()
    with pytest.raises(ProjectError):
        validate_benchmark_dir(output_dir, require_approved=False, write_report=False)


def test_validate_benchmark_require_approved_blocks_pending_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset_dir = tmp_path / "dataset"
    output_dir = tmp_path / "benchmark"
    dataset_dir.mkdir()
    (dataset_dir / "dataset_manifest.json").write_text(
        json.dumps({"dataset_version": "0.1.0"}) + "\n", encoding="utf-8"
    )

    monkeypatch.setattr("semplan.benchmark.generator.load_dataset", lambda *_args: {})
    monkeypatch.setattr("semplan.benchmark.generator.create_engine", lambda *_args: FakeEngine())
    generate_smoke_benchmark(
        output_dir,
        dataset_dir,
        overwrite=False,
        database_url="postgresql+psycopg://fake",
    )

    with pytest.raises(ProjectError):
        validate_benchmark_dir(output_dir, require_approved=True, write_report=False)


def test_prepare_f7_primary_benchmark_freezes_hidden_split(tmp_path: Path) -> None:
    output_dir = tmp_path / "f7_primary"

    result = prepare_f7_primary_benchmark(
        PROJECT_BENCHMARK_DIR,
        output_dir,
        overwrite=False,
    )
    report = validate_benchmark_dir(
        output_dir,
        require_approved=True,
        allow_hidden=True,
        write_report=False,
    )

    cases = validate_benchmark_dir(
        output_dir,
        require_approved=False,
        allow_hidden=True,
        write_report=False,
    )
    manifest = json.loads((output_dir / "benchmark_manifest.json").read_text(encoding="utf-8"))
    lineage = json.loads(
        (output_dir / "review/f7_hidden_lineage_map.json").read_text(encoding="utf-8")
    )
    exclusions = json.loads(
        (output_dir / "review/f7_scientific_exclusions.json").read_text(encoding="utf-8")
    )

    assert result["case_count"] == 49
    assert result["scientific_case_count"] == 17
    assert result["split_counts"]["test_hidden"] == 8
    assert report["status"] == "passed"
    assert cases["status"] == "passed"
    assert manifest["state"] == "frozen"
    assert manifest["hidden_included"] is True
    assert manifest["review_summary"] == {"approved": 98}
    assert all(entry["source_case_id"] != "ADV-SMK-000001" for entry in lineage["lineage"])
    assert exclusions["excluded"][0]["case_id"] == "ADV-SMK-000001"

    with pytest.raises(ProjectError):
        validate_benchmark_dir(output_dir, require_approved=True, write_report=False)


def test_release_target_matrix_matches_normative_release_size() -> None:
    target = release_target_matrix()
    specs = build_release_specs()

    assert target["total_cases"] == 1800
    assert target["split_targets"] == {
        "development": 300,
        "validation": 300,
        "test_public": 500,
        "test_hidden": 300,
        "multi_turn": 200,
        "adversarial": 200,
    }
    assert len(specs) == 900
    assert Counter(spec.split for spec in specs) == {
        DatasetSplit.DEVELOPMENT: 150,
        DatasetSplit.VALIDATION: 150,
        DatasetSplit.TEST_PUBLIC: 250,
        DatasetSplit.TEST_HIDDEN: 150,
        DatasetSplit.MULTI_TURN: 100,
        DatasetSplit.ADVERSARIAL: 100,
    }

    core_counts = Counter(
        spec.question_class
        for spec in specs
        if spec.split
        in {
            DatasetSplit.DEVELOPMENT,
            DatasetSplit.VALIDATION,
            DatasetSplit.TEST_PUBLIC,
            DatasetSplit.TEST_HIDDEN,
        }
    )
    assert core_counts[QuestionClass.GROUPED_AGGREGATION] == 107
    assert core_counts[QuestionClass.OUT_OF_SCOPE] == 21


def test_generate_release_scale_benchmark_writes_target_artifacts_without_real_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset_dir = tmp_path / "dataset"
    output_dir = tmp_path / "release"
    dataset_dir.mkdir()
    (dataset_dir / "dataset_manifest.json").write_text(
        json.dumps({"dataset_version": "0.1.0"}) + "\n", encoding="utf-8"
    )

    monkeypatch.setattr("semplan.benchmark.release_scale.load_dataset", lambda *_args: {})
    monkeypatch.setattr(
        "semplan.benchmark.release_scale.create_engine", lambda *_args: FakeEngine()
    )

    manifest = generate_release_scale_benchmark(
        output_dir,
        dataset_dir,
        overwrite=False,
        database_url="postgresql+psycopg://fake",
    )
    release_report = validate_release_scale_benchmark(
        output_dir,
        pilot_dir=None,
        write_report=False,
    )

    assert manifest.case_count == 1800
    assert manifest.benchmark_version == RELEASE_SCALE_BENCHMARK_VERSION
    assert manifest.hidden_included is True
    assert manifest.state == "frozen"
    assert manifest.language_counts == {Locale.EN_US: 900, Locale.PT_BR: 900}
    assert manifest.review_summary == {ReviewStatus.PENDING_AUTHOR_REVIEW: 3600}
    assert release_report["status"] == "passed"
    assert (output_dir / "sequences/multi_turn_sequences.jsonl").is_file()


def test_approve_benchmark_reviews_records_explicit_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset_dir = tmp_path / "dataset"
    output_dir = tmp_path / "benchmark"
    dataset_dir.mkdir()
    (dataset_dir / "dataset_manifest.json").write_text(
        json.dumps({"dataset_version": "0.1.0"}) + "\n", encoding="utf-8"
    )

    monkeypatch.setattr("semplan.benchmark.generator.load_dataset", lambda *_args: {})
    monkeypatch.setattr("semplan.benchmark.generator.create_engine", lambda *_args: FakeEngine())
    generate_smoke_benchmark(
        output_dir,
        dataset_dir,
        overwrite=False,
        database_url="postgresql+psycopg://fake",
    )

    result = approve_benchmark_reviews(
        output_dir,
        reviewer="Bruno Santos Teixeira",
        reviewed_at=datetime(2026, 8, 6, 12, 0, tzinfo=UTC),
        note="Synthetic unit-test approval fixture.",
    )
    report = validate_benchmark_dir(output_dir, require_approved=True, write_report=False)
    first_case = json.loads(
        (output_dir / "cases/development.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )

    assert result["case_count"] == 50
    assert report["status"] == "passed"
    assert report["review_counts"] == {"approved": 100}
    assert first_case["review"]["status"] == "approved"
    assert first_case["review"]["reviewer"] == "Bruno Santos Teixeira"


def test_approve_benchmark_reviews_allows_hidden_when_explicitly_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset_dir = tmp_path / "dataset"
    output_dir = tmp_path / "benchmark"
    dataset_dir.mkdir()
    (dataset_dir / "dataset_manifest.json").write_text(
        json.dumps({"dataset_version": "0.1.0"}) + "\n", encoding="utf-8"
    )

    monkeypatch.setattr("semplan.benchmark.generator.load_dataset", lambda *_args: {})
    monkeypatch.setattr("semplan.benchmark.generator.create_engine", lambda *_args: FakeEngine())
    generate_smoke_benchmark(
        output_dir,
        dataset_dir,
        overwrite=False,
        database_url="postgresql+psycopg://fake",
    )
    for path in sorted((output_dir / "cases").glob("*.jsonl")):
        hidden_lines = [
            canonical_json(
                load_case_json(line)
                .model_copy(update={"split": DatasetSplit.TEST_HIDDEN})
                .model_dump(mode="json")
            )
            for line in path.read_text(encoding="utf-8").splitlines()
            if line
        ]
        path.write_text("\n".join(hidden_lines) + "\n", encoding="utf-8", newline="\n")

    with pytest.raises(ProjectError):
        approve_benchmark_reviews(
            output_dir,
            reviewer="Bruno Santos Teixeira",
            reviewed_at=datetime(2026, 8, 6, 12, 0, tzinfo=UTC),
            note="Synthetic unit-test approval fixture.",
        )

    result = approve_benchmark_reviews(
        output_dir,
        reviewer="Bruno Santos Teixeira",
        reviewed_at=datetime(2026, 8, 6, 12, 0, tzinfo=UTC),
        note="Synthetic unit-test approval fixture.",
        allow_hidden=True,
    )
    report = validate_benchmark_dir(
        output_dir,
        require_approved=True,
        allow_hidden=True,
        write_report=False,
    )

    assert result["case_count"] == 50
    assert report["status"] == "passed"
    assert report["allow_hidden"] is True
    assert report["review_counts"] == {"approved": 100}


def test_approve_benchmark_reviews_rejects_missing_metadata(tmp_path: Path) -> None:
    for reviewer, reviewed_at, note in [
        ("", datetime(2026, 8, 6, 12, 0, tzinfo=UTC), "approved"),
        ("Bruno Santos Teixeira", datetime(2026, 8, 6, 12, 0), "approved"),
        ("Bruno Santos Teixeira", datetime(2026, 8, 6, 12, 0, tzinfo=UTC), ""),
    ]:
        with pytest.raises(ValueError):
            approve_benchmark_reviews(
                tmp_path,
                reviewer=reviewer,
                reviewed_at=reviewed_at,
                note=note,
            )


def test_load_case_json_parses_case_line() -> None:
    line = (
        (PROJECT_BENCHMARK_DIR / "cases/development.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )

    case = load_case_json(line)

    assert case.case_id == "DEV-SMK-000001"


def test_approve_benchmark_cli_rejects_naive_timestamp(capsys) -> None:  # type: ignore[no-untyped-def]
    exit_code = cli_main(
        [
            "approve-benchmark",
            str(PROJECT_BENCHMARK_DIR),
            "--reviewer",
            "Bruno Santos Teixeira",
            "--reviewed-at",
            "2026-08-06T12:00:00",
            "--note",
            "approval without timezone should fail",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "timezone" in captured.out


def test_canonical_value_formats_supported_scalar_types() -> None:
    assert _canonical_value(Decimal("1.235"), "usd") == "1.24"
    assert _canonical_value(Decimal("0.1234561"), "ratio") == "0.123456"
    assert _canonical_value(Decimal("12"), "count") == 12
    assert _canonical_value(date(2026, 8, 1), None) == "2026-08-01"
    assert _canonical_value("North", None) == "North"


def test_canonical_value_rejects_unknown_type() -> None:
    with pytest.raises(TypeError):
        _canonical_value(object(), None)
