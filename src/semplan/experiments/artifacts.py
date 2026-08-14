"""Canonical experiment artifact writing and validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from semplan.contracts import ArtifactRef, ResultRecord
from semplan.data_generation.writer import canonical_json, sha256_file
from semplan.errors import ErrorCode, ProjectError
from semplan.experiments.manifest import validate_manifest_copy


def write_json_artifact(root: Path, relative_path: str, payload: object) -> ArtifactRef:
    path = _safe_path(root, relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    serializable: object
    if isinstance(payload, BaseModel):
        serializable = payload.model_dump(mode="json")
    else:
        serializable = payload
    path.write_text(canonical_json(serializable) + "\n", encoding="utf-8", newline="\n")
    return ArtifactRef(path=relative_path, sha256=f"sha256:{sha256_file(path)}")


def write_text_artifact(root: Path, relative_path: str, text: str) -> ArtifactRef:
    path = _safe_path(root, relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return ArtifactRef(path=relative_path, sha256=f"sha256:{sha256_file(path)}")


def write_result_record(root: Path, record: ResultRecord) -> ArtifactRef:
    ref = write_json_artifact(
        root,
        f"records/{record.work_item_id.removeprefix('sha256:')}.json",
        record,
    )
    rebuild_result_jsonl(root)
    return ref


def rebuild_result_jsonl(root: Path) -> ArtifactRef:
    records = sorted(
        (
            ResultRecord.model_validate_json(path.read_text(encoding="utf-8"))
            for path in (root / "records").glob("*.json")
        ),
        key=lambda record: (record.case_id, record.approach.value, record.repetition),
    )
    path = root / "records" / "result_records.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(canonical_json(record.model_dump(mode="json")) + "\n")
    return ArtifactRef(
        path="records/result_records.jsonl",
        sha256=f"sha256:{sha256_file(path)}",
    )


def read_result_records(root: Path) -> list[ResultRecord]:
    path = root / "records" / "result_records.jsonl"
    if not path.exists():
        return []
    records: list[ResultRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line:
            records.append(ResultRecord.model_validate_json(line))
    return records


def validate_experiment_directory(root: Path) -> dict[str, Any]:
    manifest = validate_manifest_copy(root)
    records = read_result_records(root)
    errors: list[str] = []
    for record in records:
        _validate_record_refs(root, record, errors)
    if errors:
        raise ProjectError(
            ErrorCode.CFG_INVALID,
            "Experiment artifact validation failed",
            detail={"errors": errors[:20], "error_count": len(errors)},
        )
    return {
        "schema_version": "1.0",
        "ok": True,
        "run_id": manifest.run_id,
        "record_count": len(records),
        "manifest_status": manifest.status.value,
    }


def artifact_payload(root: Path, ref: ArtifactRef) -> dict[str, Any]:
    path = _safe_path(root, ref.path)
    expected_hash = f"sha256:{sha256_file(path)}"
    if expected_hash != ref.sha256:
        raise ProjectError(
            ErrorCode.CFG_INVALID,
            "Artifact hash mismatch",
            detail={"path": ref.path, "expected": ref.sha256, "actual": expected_hash},
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ProjectError(ErrorCode.CFG_INVALID, "Artifact payload must be a JSON object")
    return raw


def _validate_record_refs(root: Path, record: ResultRecord, errors: list[str]) -> None:
    refs: list[ArtifactRef] = []
    if record.provider is not None:
        refs.extend([record.provider.request_ref, record.provider.response_ref])
    if record.prediction.artifact_ref is not None:
        refs.append(record.prediction.artifact_ref)
    if record.execution.result_ref is not None:
        refs.append(record.execution.result_ref)
    refs.append(record.score_ref)
    for ref in refs:
        path = _safe_path(root, ref.path)
        if not path.exists():
            errors.append(f"missing:{ref.path}")
            continue
        actual = f"sha256:{sha256_file(path)}"
        if actual != ref.sha256:
            errors.append(f"hash:{ref.path}")


def _safe_path(root: Path, relative_path: str) -> Path:
    root_resolved = root.resolve()
    path = (root / relative_path).resolve(strict=False)
    try:
        path.relative_to(root_resolved)
    except ValueError as exc:
        raise ProjectError(
            ErrorCode.CFG_INVALID,
            "Artifact path escapes run directory",
            detail={"path": relative_path},
        ) from exc
    return path
