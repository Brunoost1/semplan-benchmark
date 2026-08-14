from __future__ import annotations

from datetime import UTC, datetime, timedelta
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
from semplan.costs import BudgetController, BudgetLedger, actual_response_cost, load_price_table
from semplan.costs.pricing import estimate_request_cost
from semplan.errors import ProjectError
from semplan.providers import BudgetedProvider


def _price_table(checked_at: datetime | None = None) -> PriceTable:
    return PriceTable(
        schema_version="1.0",
        provider="openai",
        source="unit-test price table",
        checked_at_utc=checked_at or datetime.now(UTC),
        currency="USD",
        model_prices={
            "gpt-5.6-luna": ModelPricing(
                input_per_million_usd=Decimal("1.00"),
                output_per_million_usd=Decimal("2.00"),
                cached_input_per_million_usd=Decimal("0.10"),
            )
        },
    )


def _manifest(*, budget: Decimal = Decimal("1.00"), allow_paid: bool = True) -> RunManifest:
    return RunManifest(
        schema_version="1.0",
        run_id="unit-test-manifest",
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
        budget_usd=budget,
        price_table_sha256="sha256:" + ("1" * 64),
        execution_policy_sha256="sha256:" + ("2" * 64),
        mode="synchronous",
        allow_paid=allow_paid,
    )


def _request(max_output_tokens: int = 100) -> ProviderRequest:
    return ProviderRequest(
        schema_version="1.0",
        provider="openai",
        model="gpt-5.6-luna",
        prompt_id="p",
        prompt_sha256="sha256:" + ("a" * 64),
        system="system prompt",
        inputs=["hello world"],
        output_schema_ref="direct_sql.schema.json",
        output_schema_sha256="sha256:" + ("b" * 64),
        inference_parameters={"max_output_tokens": max_output_tokens},
        timeout_seconds=30,
        metadata={},
        idempotency_hash="sha256:" + ("c" * 64),
    )


def _response() -> ProviderResponse:
    return ProviderResponse(
        schema_version="1.0",
        provider="openai",
        model="gpt-5.6-luna",
        response_id="resp-1",
        finish_status=ProviderFinishStatus.STOP,
        raw_payload={"id": "resp-1"},
        parsed_payload={"ok": True},
        usage=ProviderUsage(
            input_tokens=100,
            output_tokens=50,
            cached_input_tokens=40,
            reasoning_tokens=10,
        ),
        cost={"estimated_usd": "0", "currency": "USD"},
        timing_ms=0,
        attempts=1,
        refusal=None,
    )


def test_budget_preflight_requires_fresh_prices_and_paid_flags() -> None:
    stale = _price_table(datetime.now(UTC) - timedelta(days=30))
    budget = BudgetController(
        run_manifest=_manifest(),
        price_table=stale,
        allow_paid=True,
        api_key_present=True,
    )

    with pytest.raises(ProjectError):
        budget.preflight(_request())

    with pytest.raises(ProjectError):
        BudgetController(
            run_manifest=_manifest(),
            price_table=_price_table(),
            allow_paid=False,
            api_key_present=True,
        ).preflight(_request())

    with pytest.raises(ProjectError):
        BudgetController(
            run_manifest=_manifest(),
            price_table=_price_table(),
            allow_paid=True,
            api_key_present=False,
        ).preflight(_request())

    with pytest.raises(ProjectError):
        BudgetController(
            run_manifest=_manifest(budget=Decimal("0")),
            price_table=_price_table(),
            allow_paid=True,
            api_key_present=True,
        ).preflight(_request())

    budget = BudgetController(
        run_manifest=_manifest(allow_paid=False),
        price_table=_price_table(),
        allow_paid=True,
        api_key_present=True,
    )
    with pytest.raises(ProjectError):
        budget.preflight(_request())


def test_budgeted_provider_aborts_before_dispatch_when_over_budget() -> None:
    class CountingProvider:
        calls = 0

        def complete(self, request: ProviderRequest) -> ProviderResponse:
            self.calls += 1
            return _response()

        def estimate_cost(self, request: ProviderRequest):
            return {"estimated_usd": "0", "currency": "USD"}

        def healthcheck(self):
            return {"status": "OK", "provider": "fake"}

    inner = CountingProvider()
    budget = BudgetController(
        run_manifest=_manifest(budget=Decimal("0.000001")),
        price_table=_price_table(),
        allow_paid=True,
        api_key_present=True,
    )
    provider = BudgetedProvider(inner, budget)

    with pytest.raises(ProjectError):
        provider.complete(_request(max_output_tokens=10000))

    assert inner.calls == 0


def test_budget_ledger_refuses_to_cross_monthly_limit(tmp_path) -> None:  # type: ignore[no-untyped-def]
    ledger = BudgetLedger(tmp_path / "ledger.json", monthly_limit_usd=Decimal("20.00"))
    ledger.append_response(_response(), Decimal("19.90"))

    with pytest.raises(ProjectError):
        ledger.append_response(_response(), Decimal("0.20"))


def test_budget_ledger_rejects_malformed_files(tmp_path) -> None:  # type: ignore[no-untyped-def]
    invalid_json = tmp_path / "invalid.json"
    invalid_json.write_text("{", encoding="utf-8")
    with pytest.raises(ProjectError):
        BudgetLedger(invalid_json).total_spend_usd()

    malformed = tmp_path / "malformed.json"
    malformed.write_text('{"entries": {}}\n', encoding="utf-8")
    with pytest.raises(ProjectError):
        BudgetLedger(malformed).total_spend_usd()


def test_actual_cost_accounts_for_cached_and_reasoning_tokens() -> None:
    cost = actual_response_cost(_response(), _price_table())

    assert cost.estimated_usd == Decimal("0.000184")


def test_price_table_loader_and_unknown_model_errors(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "prices.json"
    path.write_text(_price_table().model_dump_json(), encoding="utf-8")

    loaded = load_price_table(path)
    assert loaded.provider == "openai"

    request = _request().model_copy(update={"model": "missing-model"})
    with pytest.raises(ProjectError):
        estimate_request_cost(request, loaded)

    with pytest.raises(ProjectError):
        load_price_table(tmp_path / "missing.json")


def test_budget_records_actual_response_and_ledger(tmp_path) -> None:  # type: ignore[no-untyped-def]
    budget = BudgetController(
        run_manifest=_manifest(budget=Decimal("1.00")),
        price_table=_price_table(),
        allow_paid=True,
        api_key_present=True,
        ledger=BudgetLedger(tmp_path / "ledger.json"),
    )

    actual = budget.record_response(_response())

    assert actual > Decimal("0")
    assert budget.current_run_spend_usd == actual
    assert (tmp_path / "ledger.json").is_file()
