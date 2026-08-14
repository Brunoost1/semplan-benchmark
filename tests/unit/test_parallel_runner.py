from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from semplan.contracts import Approach, DatasetSplit
from semplan.experiments.manifest import build_fake_pilot_manifest, write_run_manifest
from semplan.experiments.parallel_runner import run_experiment_parallel
from semplan.experiments.runner import validate_run_dir

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_DIR = PROJECT_ROOT / "data/benchmark/f3_smoke"
PRICE_TABLE = PROJECT_ROOT / "configs/pricing/openai_stale_example.json"


def test_parallel_runner_executes_fake_items_and_resumes(tmp_path: Path) -> None:
    base_manifest = build_fake_pilot_manifest(
        run_id="unit-parallel-resume",
        benchmark_dir=BENCHMARK_DIR,
        price_table_path=PRICE_TABLE,
    )
    manifest = base_manifest.model_copy(
        update={
            "approaches": [Approach.A3],
            "prompts": {Approach.A3: base_manifest.prompts[Approach.A3]},
            "splits": [DatasetSplit.DEVELOPMENT],
            "budget_usd": Decimal("0"),
        }
    )
    manifest_path = tmp_path / "manifest.json"
    run_dir = tmp_path / "run"
    write_run_manifest(manifest, manifest_path, overwrite=True)

    first = run_experiment_parallel(
        manifest_path=manifest_path,
        benchmark_dir=BENCHMARK_DIR,
        output_dir=run_dir,
        resume=True,
        max_items=2,
        workers=2,
        progress_interval_seconds=1,
    )
    second = run_experiment_parallel(
        manifest_path=manifest_path,
        benchmark_dir=BENCHMARK_DIR,
        output_dir=run_dir,
        resume=True,
        workers=2,
        progress_interval_seconds=1,
    )

    assert first["status"] == "interrupted"
    assert second["status"] == "completed"
    assert validate_run_dir(run_dir)["record_count"] == 30
    ledger = json.loads((run_dir / "work_ledger.json").read_text(encoding="utf-8"))
    assert {item["status"] for item in ledger["work_items"].values()} == {"completed"}
