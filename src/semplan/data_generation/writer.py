"""Canonical dataset writers and manifest hashing."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from semplan.catalog import load_catalog
from semplan.data_generation.models import (
    DATASET_SCHEMA_VERSION,
    DATASET_VERSION,
    GENERATOR_VERSION,
    TABLE_COLUMNS,
    TABLE_ORDER,
    GeneratedDataset,
    RowValue,
)


def write_dataset(
    dataset: GeneratedDataset, output_dir: Path, *, overwrite: bool
) -> dict[str, Any]:
    if output_dir.exists():
        if not overwrite:
            raise FileExistsError(f"Output directory already exists: {output_dir}")
        shutil.rmtree(output_dir)
    (output_dir / "tables").mkdir(parents=True)
    (output_dir / "parquet").mkdir(parents=True)
    (output_dir / "private").mkdir(parents=True)

    for table in TABLE_ORDER:
        _write_jsonl(output_dir / "tables" / f"{table}.jsonl", dataset.rows[table])
        _write_parquet(output_dir / "parquet" / f"{table}.parquet", dataset.rows[table])

    _write_json(output_dir / "pattern_manifest.json", dataset.pattern_manifest)
    _write_json(output_dir / "private" / "generation_ledger.json", dataset.private_ledger)

    manifest = build_manifest(dataset, output_dir)
    _write_json(output_dir / "dataset_manifest.json", manifest)
    return manifest


def build_manifest(dataset: GeneratedDataset, output_dir: Path) -> dict[str, Any]:
    catalog_hash = load_catalog(Path("catalog")).sha256()
    file_hashes = {
        str(path.relative_to(output_dir)): sha256_file(path)
        for path in sorted(output_dir.rglob("*"))
        if path.is_file() and path.name != "dataset_manifest.json"
    }
    row_counts = {table: len(dataset.rows[table]) for table in TABLE_ORDER}
    config_payload = {
        "profile": dataset.profile.model_dump(mode="json"),
        "seed": dataset.seed,
        "generator_version": GENERATOR_VERSION,
    }
    config_hash = hashlib.sha256(canonical_json(config_payload).encode("utf-8")).hexdigest()
    return {
        "schema_version": DATASET_SCHEMA_VERSION,
        "dataset_version": DATASET_VERSION,
        "state": "validated",
        "profile": dataset.profile.name,
        "seed": dataset.seed,
        "generator_version": GENERATOR_VERSION,
        "generator_commit": _git_commit(),
        "config_hash": f"sha256:{config_hash}",
        "catalog_hash": f"sha256:{catalog_hash}",
        "schema_hash": "sha256:managed-by-alembic-0001_foundation",
        "row_counts": row_counts,
        "child_seeds": dataset.child_seeds,
        "file_hashes": file_hashes,
    }


def refresh_manifest(output_dir: Path) -> dict[str, Any]:
    manifest_path = output_dir / "dataset_manifest.json"
    manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["file_hashes"] = {
        str(path.relative_to(output_dir)): sha256_file(path)
        for path in sorted(output_dir.rglob("*"))
        if path.is_file() and path.name != "dataset_manifest.json"
    }
    _write_json(manifest_path, manifest)
    return manifest


def canonical_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manifest_hash(output_dir: Path) -> str:
    return sha256_file(output_dir / "dataset_manifest.json")


def _write_json(path: Path, payload: object) -> None:
    path.write_text(canonical_json(payload) + "\n", encoding="utf-8", newline="\n")


def _write_jsonl(path: Path, rows: list[dict[str, RowValue]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(canonical_json(row) + "\n")


def _write_parquet(path: Path, rows: list[dict[str, RowValue]]) -> None:
    table_name = path.stem
    columns = TABLE_COLUMNS[table_name]
    payload = {column: [row[column] for row in rows] for column in columns}
    table = pa.Table.from_pydict(payload)
    pq.write_table(table, path, compression=None, use_dictionary=False, write_statistics=False)


def _git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            encoding="utf-8",
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return result.stdout.strip()
