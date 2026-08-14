from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from semplan.catalog import load_catalog
from semplan.contracts import (
    Approach,
    CanonicalResponse,
    CostEstimate,
    LocalizedText,
    ModelPricing,
    PriceTable,
    ProviderFinishStatus,
    ProviderHealth,
    ProviderHealthStatus,
    ProviderRequest,
    ProviderResponse,
    ProviderUsage,
    ResultOutcome,
    RunManifest,
)
from semplan.errors import ProjectError
from semplan.prompts import PromptRegistry
from semplan.providers import FakeProvider, ProviderCache
from semplan.smoke import CacheOnlyProvider, _runner_for_approach, run_manual_paid_smoke

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_DIR = PROJECT_ROOT / "data/benchmark/f3_smoke"


def _request(provider: str = "openai-cache") -> ProviderRequest:
    return ProviderRequest(
        schema_version="1.0",
        provider=provider,
        model="gpt-5.6-luna",
        prompt_id="semantic_request_a3_v1",
        prompt_sha256="sha256:" + ("a" * 64),
        system="system",
        inputs=["input"],
        output_schema_ref="semantic_request.schema.json",
        output_schema_sha256="sha256:" + ("b" * 64),
        inference_parameters={},
        timeout_seconds=30,
        metadata={},
        idempotency_hash="sha256:" + (("d" if provider == "openai" else "c") * 64),
    )


def _response() -> ProviderResponse:
    return ProviderResponse(
        schema_version="1.0",
        provider="openai",
        model="gpt-5.6-luna",
        response_id="resp-cache",
        finish_status=ProviderFinishStatus.STOP,
        raw_payload={"id": "resp-cache"},
        parsed_payload={"ok": True},
        usage=ProviderUsage(input_tokens=1, output_tokens=1),
        cost=CostEstimate(estimated_usd=Decimal("0.000001")),
        timing_ms=0,
        attempts=1,
        refusal=None,
    )


def _manifest() -> RunManifest:
    return RunManifest(
        schema_version="1.0",
        run_id="manual-smoke-test",
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
            "provider": "openai",
            "id": "gpt-5.6-luna",
            "reasoning_effort": "low",
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
        budget_usd=Decimal("1.00"),
        price_table_sha256="sha256:" + ("1" * 64),
        execution_policy_sha256="sha256:" + ("2" * 64),
        mode="synchronous",
        allow_paid=True,
    )


def _price_table() -> PriceTable:
    return PriceTable(
        schema_version="1.0",
        provider="openai",
        source="unit-test",
        checked_at_utc=datetime.now(UTC),
        currency="USD",
        model_prices={
            "gpt-5.6-luna": ModelPricing(
                input_per_million_usd=Decimal("1"),
                output_per_million_usd=Decimal("2"),
            )
        },
    )


class FakeRunner:
    def __init__(self, provider: CacheOnlyProvider, request: ProviderRequest) -> None:
        self.provider = provider
        self.request = request

    def run_case(self, case):  # type: ignore[no-untyped-def]
        provider_response = self.provider.complete(self.request)
        return SimpleNamespace(
            provider_response=provider_response,
            outcome=ResultOutcome.ANSWERED,
            response=CanonicalResponse(
                schema_version="1.0",
                outcome=ResultOutcome.ANSWERED,
                rows=[],
                units={},
                message=LocalizedText(
                    **{
                        "en-US": f"Replayed {case.case_id}.",
                        "pt-BR": f"Reexecutou {case.case_id}.",
                    }
                ),
            ),
        )


def test_manual_paid_smoke_replay_only_uses_completed_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path = tmp_path / "manifest.json"
    price_path = tmp_path / "prices.json"
    manifest_path.write_text(_manifest().model_dump_json(), encoding="utf-8")
    price_path.write_text(_price_table().model_dump_json(), encoding="utf-8")
    cache = ProviderCache(tmp_path / "cache")
    request = _request(provider="openai")
    reservation = cache.reserve(request)
    cache.complete(reservation, _response())

    monkeypatch.setattr(
        "semplan.smoke._runner_for_approach",
        lambda approach, **kwargs: FakeRunner(kwargs["provider"], request),
    )

    report = run_manual_paid_smoke(
        benchmark_dir=BENCHMARK_DIR,
        case_id="DEV-SMK-000001",
        approach=Approach.A3,
        manifest_path=manifest_path,
        price_table_path=price_path,
        cache_dir=tmp_path / "cache",
        output_dir=tmp_path / "out",
        allow_paid=False,
        replay_only=True,
    )

    assert report["replay_only"] is True
    assert report["incremental_paid_calls"] == 0
    assert (tmp_path / "out" / "manual_paid_smoke_report.json").is_file()


def test_manual_paid_smoke_live_mode_uses_cache_without_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path = tmp_path / "manifest.json"
    price_path = tmp_path / "prices.json"
    manifest_path.write_text(_manifest().model_dump_json(), encoding="utf-8")
    price_path.write_text(_price_table().model_dump_json(), encoding="utf-8")
    request = _request(provider="openai")
    cache = ProviderCache(tmp_path / "cache")
    reservation = cache.reserve(request)
    cache.complete(reservation, _response())
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-printed")
    monkeypatch.setattr(
        "semplan.smoke._runner_for_approach",
        lambda approach, **kwargs: FakeRunner(kwargs["provider"], request),
    )

    report = run_manual_paid_smoke(
        benchmark_dir=BENCHMARK_DIR,
        case_id="DEV-SMK-000001",
        approach=Approach.A3,
        manifest_path=manifest_path,
        price_table_path=price_path,
        cache_dir=tmp_path / "cache",
        output_dir=tmp_path / "out",
        allow_paid=True,
        replay_only=False,
    )

    assert report["replay_only"] is False
    assert report["incremental_paid_calls"] == 0


def test_manual_paid_smoke_requires_allow_paid(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    price_path = tmp_path / "prices.json"
    manifest_path.write_text(_manifest().model_dump_json(), encoding="utf-8")
    price_path.write_text(_price_table().model_dump_json(), encoding="utf-8")

    with pytest.raises(ProjectError):
        run_manual_paid_smoke(
            benchmark_dir=BENCHMARK_DIR,
            case_id="DEV-SMK-000001",
            approach=Approach.A3,
            manifest_path=manifest_path,
            price_table_path=price_path,
            cache_dir=tmp_path / "cache",
            output_dir=tmp_path / "out",
            allow_paid=False,
            replay_only=False,
        )


def test_cache_only_provider_reports_health_and_miss(tmp_path: Path) -> None:
    provider = CacheOnlyProvider(ProviderCache(tmp_path))
    assert provider.healthcheck() == ProviderHealth(
        status=ProviderHealthStatus.OK,
        provider="openai-cache",
    )
    assert provider.estimate_cost(_request()).estimated_usd == Decimal("0")

    with pytest.raises(ProjectError):
        provider.complete(_request())


def test_smoke_runner_factory_covers_all_approaches() -> None:
    catalog = load_catalog(PROJECT_ROOT / "catalog")
    prompts = PromptRegistry.load(PROJECT_ROOT / "prompts")
    provider = FakeProvider({})

    assert (
        _runner_for_approach(
            Approach.A1,
            provider=provider,
            catalog=catalog,
            prompts=prompts,
            database_url=None,
            provider_name="fake",
            model_name="fake",
        ).__class__.__name__
        == "DirectSqlRunner"
    )
    assert (
        _runner_for_approach(
            Approach.A2,
            provider=provider,
            catalog=catalog,
            prompts=prompts,
            database_url=None,
            provider_name="fake",
            model_name="fake",
        ).__class__.__name__
        == "ToolAgentRunner"
    )
    assert (
        _runner_for_approach(
            Approach.A3,
            provider=provider,
            catalog=catalog,
            prompts=prompts,
            database_url=None,
            provider_name="fake",
            model_name="fake",
        ).__class__.__name__
        == "SemanticPlanRunner"
    )
