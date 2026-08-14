from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from semplan.contracts import (
    ModelPricing,
    PriceTable,
    ProviderFinishStatus,
    ProviderRequest,
    ProviderResponse,
    ProviderUsage,
    RunManifest,
)
from semplan.costs import BudgetController
from semplan.errors import ErrorCode, ProjectError
from semplan.providers import BudgetedProvider, CachedProvider, ProviderCache


def _request() -> ProviderRequest:
    return ProviderRequest(
        schema_version="1.0",
        provider="fake",
        model="fake",
        prompt_id="p",
        prompt_sha256="sha256:" + ("a" * 64),
        system="system",
        inputs=["input"],
        output_schema_ref="semantic_request.schema.json",
        output_schema_sha256="sha256:" + ("b" * 64),
        inference_parameters={},
        timeout_seconds=30,
        metadata={},
        idempotency_hash="sha256:" + ("c" * 64),
    )


def _response(response_id: str = "resp-1") -> ProviderResponse:
    return ProviderResponse(
        schema_version="1.0",
        provider="fake",
        model="fake",
        response_id=response_id,
        finish_status=ProviderFinishStatus.STOP,
        raw_payload={"id": response_id},
        parsed_payload={"schema_version": "1.0", "ok": True},
        usage=ProviderUsage(input_tokens=1, output_tokens=1),
        cost={"estimated_usd": Decimal("0"), "currency": "USD"},
        timing_ms=0,
        attempts=1,
        refusal=None,
    )


def _price_table() -> PriceTable:
    return PriceTable(
        schema_version="1.0",
        provider="openai",
        source="unit-test",
        checked_at_utc=datetime.now(UTC),
        currency="USD",
        model_prices={
            "fake": ModelPricing(
                input_per_million_usd=Decimal("1"),
                output_per_million_usd=Decimal("2"),
            )
        },
    )


def _manifest() -> RunManifest:
    return RunManifest(
        schema_version="1.0",
        run_id="cache-test",
        status="frozen",
        created_at_utc=datetime.now(UTC),
        code_commit="a" * 40,
        dirty_tree=False,
        non_reportable=True,
        dataset_version="0.1.0",
        dataset_manifest_sha256="sha256:" + ("b" * 64),
        benchmark_manifest_sha256="sha256:" + ("c" * 64),
        catalog_sha256="sha256:" + ("d" * 64),
        approaches=["A3"],
        model={
            "provider": "fake",
            "id": "fake",
            "reasoning_effort": "none",
            "parameters": {"temperature": "0"},
        },
        prompts={
            "A3": {
                "id": "semantic_request_a3_v1",
                "sha256": "sha256:" + ("e" * 64),
                "output_schema_ref": "semantic_request.schema.json",
                "output_schema_sha256": "sha256:" + ("f" * 64),
            }
        },
        splits=["development"],
        repetitions=1,
        randomization_seed=20260806,
        budget_usd=Decimal("1"),
        price_table_sha256="sha256:" + ("1" * 64),
        execution_policy_sha256="sha256:" + ("2" * 64),
        mode="synchronous",
        allow_paid=True,
    )


class CountingProvider:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        self.calls += 1
        return _response(f"resp-{self.calls}")

    def estimate_cost(self, request: ProviderRequest):
        return {"estimated_usd": "0", "currency": "USD"}

    def healthcheck(self):
        return {"status": "OK", "provider": "fake"}


def test_cached_provider_reuses_completed_response_without_second_dispatch(tmp_path) -> None:  # type: ignore[no-untyped-def]
    inner = CountingProvider()
    provider = CachedProvider(inner, ProviderCache(tmp_path))
    request = _request()

    first = provider.complete(request)
    second = provider.complete(request)

    assert first.response_id == "resp-1"
    assert second.response_id == "resp-1"
    assert inner.calls == 1


def test_cache_inflight_state_blocks_duplicate_dispatch(tmp_path) -> None:  # type: ignore[no-untyped-def]
    cache = ProviderCache(tmp_path)
    request = _request()
    reservation = cache.reserve(request)
    cache.mark_in_flight(reservation)
    inner = CountingProvider()

    with pytest.raises(ProjectError):
        CachedProvider(inner, cache).complete(request)

    assert inner.calls == 0


def test_cache_detects_response_hash_mismatch(tmp_path) -> None:  # type: ignore[no-untyped-def]
    cache = ProviderCache(tmp_path)
    request = _request()
    reservation = cache.reserve(request)
    cache.complete(reservation, _response())
    response_path = next(tmp_path.rglob("raw_response.json"))
    response_path.write_text('{"changed": true}\n', encoding="utf-8")

    with pytest.raises(ProjectError):
        cache.lookup(request)


def test_cache_records_retryable_failure_state(tmp_path) -> None:  # type: ignore[no-untyped-def]
    cache = ProviderCache(tmp_path)
    reservation = cache.reserve(_request())

    cache.fail(reservation, retryable=True, reason="timeout")

    entry = next(tmp_path.rglob("entry.json")).read_text(encoding="utf-8")
    assert "failed_retryable" in entry


def test_retryable_cache_entry_can_be_retried_without_completed_cache_repurchase(
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    cache = ProviderCache(tmp_path)
    request = _request()
    reservation = cache.reserve(request)
    cache.fail(reservation, retryable=True, reason="rate_limit")
    inner = CountingProvider()

    response = CachedProvider(inner, cache).complete(request)
    cached = CachedProvider(inner, cache).complete(request)

    assert response.response_id == "resp-1"
    assert cached.response_id == "resp-1"
    assert inner.calls == 1
    assert next(tmp_path.rglob("attempt_001.json")).is_file()
    assert "completed" in next(tmp_path.rglob("entry.json")).read_text(encoding="utf-8")


def test_cached_provider_estimate_is_zero_on_cache_hit(tmp_path) -> None:  # type: ignore[no-untyped-def]
    cache = ProviderCache(tmp_path)
    request = _request()
    reservation = cache.reserve(request)
    cache.complete(reservation, _response())

    estimate = CachedProvider(CountingProvider(), cache).estimate_cost(request)

    assert estimate.estimated_usd == Decimal("0")


def test_cached_provider_records_project_error_failure(tmp_path) -> None:  # type: ignore[no-untyped-def]
    class FailingProvider:
        def complete(self, request: ProviderRequest) -> ProviderResponse:
            raise ProjectError(
                ErrorCode.OUTPUT_SCHEMA_INVALID,
                "terminal failure",
                detail={"error_type": "SchemaError"},
            )

        def estimate_cost(self, request: ProviderRequest):
            return {"estimated_usd": "0", "currency": "USD"}

        def healthcheck(self):
            return {"status": "OK", "provider": "fake"}

    with pytest.raises(ProjectError):
        CachedProvider(FailingProvider(), ProviderCache(tmp_path)).complete(_request())

    entry = next(tmp_path.rglob("entry.json")).read_text(encoding="utf-8")
    assert "failed_terminal" in entry
    assert "SchemaError" in entry


def test_cached_provider_records_untyped_exception_failure(tmp_path) -> None:  # type: ignore[no-untyped-def]
    class FailingProvider:
        def complete(self, request: ProviderRequest) -> ProviderResponse:
            raise RuntimeError("boom")

        def estimate_cost(self, request: ProviderRequest):
            return {"estimated_usd": "0", "currency": "USD"}

        def healthcheck(self):
            return {"status": "OK", "provider": "fake"}

    with pytest.raises(RuntimeError):
        CachedProvider(FailingProvider(), ProviderCache(tmp_path)).complete(_request())

    entry = next(tmp_path.rglob("entry.json")).read_text(encoding="utf-8")
    assert "failed_retryable" in entry
    assert "RuntimeError" in entry


def test_budgeted_provider_records_success_and_estimate() -> None:
    budget = BudgetController(
        run_manifest=_manifest(),
        price_table=_price_table(),
        allow_paid=True,
        api_key_present=True,
    )
    inner = CountingProvider()
    provider = BudgetedProvider(inner, budget)

    estimate = provider.estimate_cost(_request())
    response = provider.complete(_request())

    assert estimate.estimated_usd > Decimal("0")
    assert response.response_id == "resp-1"
    assert len(provider.preflight_checks) == 2
