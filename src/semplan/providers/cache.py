"""Content-addressed provider cache and budgeted provider wrappers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from semplan.contracts import (
    BudgetCheck,
    CacheEntryState,
    CostEstimate,
    ProviderHealth,
    ProviderRequest,
    ProviderResponse,
)
from semplan.costs import BudgetController
from semplan.data_generation.writer import canonical_json, sha256_file
from semplan.errors import ErrorCode, ProjectError
from semplan.providers.base import ModelProvider


@dataclass(frozen=True)
class CacheReservation:
    key: str
    path: Path


class ProviderCache:
    """Immutable completed-response cache keyed by provider request hash."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def lookup(self, request: ProviderRequest) -> ProviderResponse | None:
        entry_path = self._entry_path(request)
        if not entry_path.exists():
            return None
        entry = self._read_entry(entry_path)
        raw_state = entry.get("state")
        if not isinstance(raw_state, str):
            raise ProjectError(ErrorCode.CFG_INVALID, "Provider cache entry is missing state")
        state = CacheEntryState(raw_state)
        if state is CacheEntryState.FAILED_RETRYABLE:
            return None
        if state is not CacheEntryState.COMPLETED:
            raise ProjectError(
                ErrorCode.BUDGET_EXCEEDED,
                "Provider cache key is not completed and cannot be dispatched",
                detail={"request_hash": request.idempotency_hash, "state": state.value},
            )
        response_path = entry_path.parent / "raw_response.json"
        expected_hash = entry.get("response_sha256")
        if expected_hash != f"sha256:{sha256_file(response_path)}":
            raise ProjectError(
                ErrorCode.CFG_INVALID,
                "Cached provider response hash mismatch",
                detail={"request_hash": request.idempotency_hash},
            )
        response = ProviderResponse.model_validate(
            json.loads(response_path.read_text(encoding="utf-8"))
        )
        return response

    def reserve(self, request: ProviderRequest) -> CacheReservation:
        key_dir = self._key_dir(request.idempotency_hash)
        try:
            key_dir.mkdir(parents=True)
        except FileExistsError as exc:
            self._archive_retryable_failure_for_reserve(request, key_dir, exc)
        self._write_entry(
            key_dir / "entry.json",
            {
                "schema_version": "1.0",
                "request_hash": request.idempotency_hash,
                "state": CacheEntryState.RESERVED.value,
            },
        )
        return CacheReservation(key=request.idempotency_hash, path=key_dir)

    def mark_in_flight(self, reservation: CacheReservation) -> None:
        self._write_entry(
            reservation.path / "entry.json",
            {
                "schema_version": "1.0",
                "request_hash": reservation.key,
                "state": CacheEntryState.IN_FLIGHT.value,
            },
        )

    def complete(self, reservation: CacheReservation, response: ProviderResponse) -> None:
        response_path = reservation.path / "raw_response.json"
        tmp_response_path = response_path.with_suffix(".json.tmp")
        tmp_response_path.write_text(
            canonical_json(response.model_dump(mode="json")) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        tmp_response_path.replace(response_path)
        self._write_entry(
            reservation.path / "entry.json",
            {
                "schema_version": "1.0",
                "request_hash": reservation.key,
                "state": CacheEntryState.COMPLETED.value,
                "response_sha256": f"sha256:{sha256_file(response_path)}",
                "provider": response.provider,
                "model": response.model,
                "response_id": response.response_id,
            },
        )

    def fail(
        self,
        reservation: CacheReservation,
        *,
        retryable: bool,
        reason: str,
        detail: dict[str, object] | None = None,
    ) -> None:
        payload: dict[str, object] = {
            "schema_version": "1.0",
            "request_hash": reservation.key,
            "state": (
                CacheEntryState.FAILED_RETRYABLE.value
                if retryable
                else CacheEntryState.FAILED_TERMINAL.value
            ),
            "reason": reason,
        }
        if detail:
            payload["detail"] = detail
        self._write_entry(reservation.path / "entry.json", payload)

    def _entry_path(self, request: ProviderRequest) -> Path:
        return self._key_dir(request.idempotency_hash) / "entry.json"

    def _key_dir(self, request_hash: str) -> Path:
        digest = request_hash.removeprefix("sha256:")
        return self.root / digest[:2] / digest

    def _archive_retryable_failure_for_reserve(
        self,
        request: ProviderRequest,
        key_dir: Path,
        cause: FileExistsError,
    ) -> None:
        entry_path = key_dir / "entry.json"
        if not entry_path.exists():
            raise ProjectError(
                ErrorCode.BUDGET_EXCEEDED,
                "Provider cache key already exists",
                detail={"request_hash": request.idempotency_hash},
            ) from cause
        entry = self._read_entry(entry_path)
        if entry.get("state") != CacheEntryState.FAILED_RETRYABLE.value:
            raise ProjectError(
                ErrorCode.BUDGET_EXCEEDED,
                "Provider cache key already exists",
                detail={"request_hash": request.idempotency_hash},
            ) from cause
        attempts_dir = key_dir / "attempts"
        attempts_dir.mkdir(exist_ok=True)
        attempt_index = 1 + len(list(attempts_dir.glob("attempt_*.json")))
        archived = attempts_dir / f"attempt_{attempt_index:03d}.json"
        entry_path.replace(archived)

    @staticmethod
    def _read_entry(path: Path) -> dict[str, object]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProjectError(
                ErrorCode.CFG_INVALID,
                "Provider cache entry cannot be read",
                detail={"path": str(path), "reason": str(exc)},
            ) from exc
        if not isinstance(raw, dict):
            raise ProjectError(ErrorCode.CFG_INVALID, "Provider cache entry is malformed")
        return raw

    @staticmethod
    def _write_entry(path: Path, payload: dict[str, object]) -> None:
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(canonical_json(payload) + "\n", encoding="utf-8", newline="\n")
        tmp_path.replace(path)


class CachedProvider:
    """Provider wrapper that uses completed cache entries before dispatch."""

    def __init__(self, provider: ModelProvider, cache: ProviderCache) -> None:
        self.provider = provider
        self.cache = cache

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        cached = self.cache.lookup(request)
        if cached is not None:
            return cached
        reservation = self.cache.reserve(request)
        try:
            self.cache.mark_in_flight(reservation)
            response = self.provider.complete(request)
            self.cache.complete(reservation, response)
            return response
        except ProjectError as exc:
            self.cache.fail(
                reservation,
                retryable=exc.record.retryable,
                reason=exc.record.message,
                detail=exc.record.detail,
            )
            raise
        except Exception as exc:
            self.cache.fail(
                reservation,
                retryable=True,
                reason=type(exc).__name__,
                detail={"error_type": type(exc).__name__},
            )
            raise

    def estimate_cost(self, request: ProviderRequest) -> CostEstimate:
        if self.cache.lookup(request) is not None:
            return CostEstimate(estimated_usd=Decimal("0"))
        return self.provider.estimate_cost(request)

    def healthcheck(self) -> ProviderHealth:
        return self.provider.healthcheck()


class BudgetedProvider:
    """Provider wrapper that preflights and records budget before/after dispatch."""

    def __init__(self, provider: ModelProvider, budget: BudgetController) -> None:
        self.provider = provider
        self.budget = budget
        self.preflight_checks: list[BudgetCheck] = []

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        check = self.budget.preflight(request)
        self.preflight_checks.append(check)
        response = self.provider.complete(request)
        self.budget.record_response(response)
        return response

    def estimate_cost(self, request: ProviderRequest) -> CostEstimate:
        check = self.budget.preflight(request)
        self.preflight_checks.append(check)
        return CostEstimate(estimated_usd=check.estimated_usd)

    def healthcheck(self) -> ProviderHealth:
        return self.provider.healthcheck()
