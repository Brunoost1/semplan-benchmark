"""Recovery helpers for superseded F7 run manifests and partial ledgers."""

from __future__ import annotations

import json
import shutil
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from semplan.benchmark.validator import load_benchmark_cases
from semplan.catalog import load_catalog
from semplan.contracts import ProviderRequest, RunManifest, WorkItemStatus
from semplan.data_generation.writer import canonical_json, sha256_file
from semplan.errors import ErrorCode, ProjectError
from semplan.experiments.manifest import (
    copy_manifest_for_run,
    load_run_manifest,
    manifest_file_hash,
    validate_manifest_for_execution,
    write_run_manifest,
)
from semplan.experiments.planning import CapturedRequest, CaptureOnlyProvider
from semplan.experiments.runner import _runner_for_item, create_work_items
from semplan.prompts import PromptRegistry


def create_superseding_hotfix_manifest(
    *,
    original_manifest_path: Path,
    output_path: Path,
    code_commit: str,
    executable_code_commit: str,
    documentation_commit: str,
    created_at_utc: str,
    supersession_reason: str = "local execution-capture hotfix for DBAPI failures",
    overwrite: bool = False,
) -> dict[str, Any]:
    """Create a frozen manifest that only supersedes source/recovery metadata."""

    original = load_run_manifest(original_manifest_path)
    original_hash = manifest_file_hash(original_manifest_path)
    environment = dict(original.environment)
    environment.update(
        {
            "supersedes_manifest_sha256": original_hash,
            "supersession_reason": supersession_reason,
            "executable_code_commit": executable_code_commit,
            "documentation_followup_commit": documentation_commit,
            "benchmark_prompts_model_scoring_hypotheses_changed": "false",
        }
    )
    manifest = original.model_copy(
        update={
            "created_at_utc": datetime.fromisoformat(created_at_utc.replace("Z", "+00:00")),
            "code_commit": code_commit,
            "dirty_tree": False,
            "environment": environment,
        }
    )
    write_run_manifest(manifest, output_path, overwrite=overwrite)
    return {
        "schema_version": "1.0",
        "ok": True,
        "path": str(output_path),
        "old_manifest_sha256": original_hash,
        "new_manifest_sha256": manifest_file_hash(output_path),
        "run_id": manifest.run_id,
        "code_commit": manifest.code_commit,
        "executable_code_commit": executable_code_commit,
    }


def reconcile_partial_run_state(
    *,
    manifest_path: Path,
    benchmark_dir: Path,
    run_dir: Path,
    cache_dir: Path,
    output_path: Path,
) -> dict[str, Any]:
    """Archive partial state and reset only recoverable ledger/cache entries."""

    manifest = load_run_manifest(manifest_path)
    validate_manifest_for_execution(manifest, benchmark_dir=benchmark_dir, allow_paid=True)
    before = _state_summary(run_dir, cache_dir)
    backup_dir = _backup_partial_state(run_dir=run_dir, cache_dir=cache_dir)
    copy_manifest_for_run(manifest_path, run_dir)

    requests = _planned_requests(manifest, benchmark_dir)
    request_by_work_item = {work_item_id: request for work_item_id, request in requests.items()}
    ledger_path = run_dir / "work_ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    if not isinstance(ledger, dict) or not isinstance(ledger.get("work_items"), dict):
        raise ProjectError(ErrorCode.CFG_INVALID, "Work ledger is malformed")

    affected: list[dict[str, Any]] = []
    work_items = ledger["work_items"]
    if not isinstance(work_items, dict):
        raise ProjectError(ErrorCode.CFG_INVALID, "Work ledger work_items is malformed")
    for work_item_id, raw_item in sorted(work_items.items()):
        if not isinstance(raw_item, dict):
            raise ProjectError(ErrorCode.CFG_INVALID, "Work ledger item is malformed")
        status = raw_item.get("status")
        request = request_by_work_item.get(str(work_item_id))
        request_hash = request.idempotency_hash if request is not None else None
        cache_state = _cache_state(cache_dir, request_hash) if request_hash is not None else None
        recoverable_terminal = (
            status == WorkItemStatus.FAILED_TERMINAL.value
            and _is_terminal_without_provider_evidence(run_dir, raw_item)
        )
        recoverable_stale_cache = status == WorkItemStatus.PENDING.value and cache_state in {
            "failed_retryable",
            "in_flight",
        }
        if (
            status
            not in {
                WorkItemStatus.RUNNING.value,
                WorkItemStatus.FAILED_RETRYABLE.value,
            }
            and not recoverable_terminal
            and not recoverable_stale_cache
        ):
            continue
        old_result_record_ref = raw_item.get("result_record_ref")
        action = "reset_to_pending"
        if status == WorkItemStatus.FAILED_RETRYABLE.value and request_hash is not None:
            _archive_cache_entry(
                cache_dir, backup_dir, request_hash, expected_state="failed_retryable"
            )
            action = "reset_retryable_and_archived_cache_entry"
        elif request_hash is not None and cache_state in {"failed_retryable", "in_flight"}:
            _archive_cache_entry(cache_dir, backup_dir, request_hash, expected_state=cache_state)
            action = f"reset_and_archived_stale_{cache_state}_cache_entry"
        elif recoverable_terminal:
            _archive_result_artifacts_for_recovery(run_dir, backup_dir, raw_item)
            if request_hash is not None:
                _archive_cache_entry(
                    cache_dir,
                    backup_dir,
                    request_hash,
                    expected_state="failed_terminal",
                )
            action = "reset_terminal_without_provider_evidence"
        raw_item["status"] = WorkItemStatus.PENDING.value
        raw_item["result_record_ref"] = None
        raw_item["updated_at"] = datetime.now(UTC).isoformat()
        affected.append(
            {
                "work_item_id": str(work_item_id),
                "case_id": raw_item.get("case_id"),
                "approach": raw_item.get("approach"),
                "repetition": raw_item.get("repetition"),
                "old_status": status,
                "new_status": WorkItemStatus.PENDING.value,
                "old_result_record_ref": old_result_record_ref,
                "request_hash": request_hash,
                "cache_state_before": cache_state,
                "action": action,
            }
        )
    ledger_path.write_text(canonical_json(ledger) + "\n", encoding="utf-8", newline="\n")
    after = _state_summary(run_dir, cache_dir)
    report = {
        "schema_version": "1.0",
        "ok": True,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "manifest": str(manifest_path),
        "manifest_sha256": manifest_file_hash(manifest_path),
        "run_dir": str(run_dir),
        "cache_dir": str(cache_dir),
        "backup_dir": str(backup_dir),
        "before": before,
        "after": after,
        "affected": affected,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(canonical_json(report) + "\n", encoding="utf-8", newline="\n")
    return report


def _planned_requests(manifest: RunManifest, benchmark_dir: Path) -> dict[str, ProviderRequest]:
    cases = load_benchmark_cases(benchmark_dir)
    case_map = {case.case_id: case for case in cases}
    catalog = load_catalog(Path("catalog"))
    prompts = PromptRegistry.load(Path("prompts"))
    planned: dict[str, ProviderRequest] = {}
    for item in create_work_items(
        manifest, [case for case in cases if case.split in manifest.splits]
    ):
        requests: list[ProviderRequest] = []
        runner = _runner_for_item(
            approach=item.approach,
            provider=CaptureOnlyProvider(requests),
            catalog=catalog,
            prompts=prompts,
            manifest=manifest,
            item=item,
            database_url=None,
        )
        try:
            runner.run_case(case_map[item.case_id])
        except CapturedRequest:
            pass
        if len(requests) != 1:
            raise ProjectError(
                ErrorCode.CFG_INVALID,
                "Could not render exactly one provider request for work item",
                detail={"work_item_id": item.work_item_id},
            )
        planned[item.work_item_id] = requests[0]
    return planned


def _backup_partial_state(*, run_dir: Path, cache_dir: Path) -> Path:
    backup_dir = run_dir / "recovery" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_dir.mkdir(parents=True, exist_ok=False)
    for relative in (
        "work_ledger.json",
        "records/result_records.jsonl",
        "budget_ledger.json",
        "run_manifest.json",
        "run_manifest.sha256",
    ):
        source = run_dir / relative
        if source.exists():
            target = backup_dir / "run_dir" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    manifest = {
        "schema_version": "1.0",
        "run_dir": str(run_dir),
        "cache_dir": str(cache_dir),
        "files": _hash_files(backup_dir),
    }
    (backup_dir / "backup_manifest.json").write_text(
        canonical_json(manifest) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return backup_dir


def _archive_cache_entry(
    cache_dir: Path,
    backup_dir: Path,
    request_hash: str,
    *,
    expected_state: str,
) -> None:
    key_dir = _cache_key_dir(cache_dir, request_hash)
    entry_path = key_dir / "entry.json"
    if not entry_path.exists():
        return
    entry = json.loads(entry_path.read_text(encoding="utf-8"))
    if entry.get("state") != expected_state:
        return
    target = backup_dir / f"cache_{expected_state}" / request_hash.removeprefix("sha256:")
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(key_dir, target)
    shutil.rmtree(key_dir)


def _archive_result_artifacts_for_recovery(
    run_dir: Path,
    backup_dir: Path,
    ledger_item: dict[str, Any],
) -> None:
    record_ref = ledger_item.get("result_record_ref")
    if not isinstance(record_ref, str):
        return
    relative_paths = [record_ref]
    record_path = run_dir / record_ref
    if record_path.exists():
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            record = {}
        if isinstance(record, dict):
            score_ref = record.get("score_ref")
            if isinstance(score_ref, dict) and isinstance(score_ref.get("path"), str):
                relative_paths.append(str(score_ref["path"]))
    for relative_path in relative_paths:
        source = run_dir / relative_path
        if source.exists():
            target = backup_dir / "recovered_terminal_records" / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)


def _is_terminal_without_provider_evidence(run_dir: Path, ledger_item: dict[str, Any]) -> bool:
    record_ref = ledger_item.get("result_record_ref")
    if not isinstance(record_ref, str):
        return True
    record_path = run_dir / record_ref
    if not record_path.exists():
        return True
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return True
    return isinstance(record, dict) and record.get("provider") is None


def _state_summary(run_dir: Path, cache_dir: Path) -> dict[str, Any]:
    ledger_counts: Counter[str] = Counter()
    ledger_path = run_dir / "work_ledger.json"
    if ledger_path.exists():
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        for item in ledger.get("work_items", {}).values():
            if isinstance(item, dict):
                ledger_counts[str(item.get("status"))] += 1
    cache_counts: Counter[str] = Counter()
    for entry_path in cache_dir.rglob("entry.json"):
        entry = json.loads(entry_path.read_text(encoding="utf-8"))
        cache_counts[str(entry.get("state"))] += 1
    return {
        "ledger_counts": dict(sorted(ledger_counts.items())),
        "cache_counts": dict(sorted(cache_counts.items())),
        "work_ledger_sha256": f"sha256:{sha256_file(ledger_path)}"
        if ledger_path.exists()
        else None,
        "result_records_sha256": f"sha256:{sha256_file(run_dir / 'records/result_records.jsonl')}"
        if (run_dir / "records/result_records.jsonl").exists()
        else None,
    }


def _cache_state(cache_dir: Path, request_hash: str) -> str | None:
    entry_path = _cache_key_dir(cache_dir, request_hash) / "entry.json"
    if not entry_path.exists():
        return None
    entry = json.loads(entry_path.read_text(encoding="utf-8"))
    return str(entry.get("state"))


def _cache_key_dir(cache_dir: Path, request_hash: str) -> Path:
    digest = request_hash.removeprefix("sha256:")
    return cache_dir / digest[:2] / digest


def _hash_files(root: Path) -> list[dict[str, str]]:
    files: list[dict[str, str]] = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "backup_manifest.json":
            files.append(
                {
                    "path": str(path.relative_to(root)),
                    "sha256": f"sha256:{sha256_file(path)}",
                }
            )
    return files
