"""Deterministic provider price-table and token-estimation helpers."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from semplan.contracts import (
    CostEstimate,
    ModelPricing,
    PriceTable,
    ProviderRequest,
    ProviderResponse,
)
from semplan.errors import ErrorCode, ProjectError

DEFAULT_OUTPUT_TOKENS = 1000


def load_price_table(path: Path) -> PriceTable:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectError(
            ErrorCode.CFG_INVALID,
            "Price table cannot be loaded",
            detail={"path": str(path), "reason": str(exc)},
        ) from exc
    return PriceTable.model_validate(raw)


def ensure_price_table_fresh(
    price_table: PriceTable,
    *,
    now: datetime | None = None,
    max_age_days: int = 7,
) -> None:
    checked_at = price_table.checked_at_utc.astimezone(UTC)
    reference = now.astimezone(UTC) if now else datetime.now(UTC)
    if checked_at > reference + timedelta(minutes=5):
        raise ProjectError(
            ErrorCode.CFG_INVALID,
            "Price table checked_at_utc is in the future",
            detail={"checked_at_utc": checked_at.isoformat()},
        )
    if reference - checked_at > timedelta(days=max_age_days):
        raise ProjectError(
            ErrorCode.CFG_INVALID,
            "Price table is stale",
            detail={
                "checked_at_utc": checked_at.isoformat(),
                "max_age_days": max_age_days,
            },
        )


def estimate_request_tokens(request: ProviderRequest) -> tuple[int, int]:
    input_tokens = _token_estimate(request.system)
    input_tokens += sum(_token_estimate(input_text) for input_text in request.inputs)
    configured_output = request.inference_parameters.get("max_output_tokens", DEFAULT_OUTPUT_TOKENS)
    output_tokens = _int_or_default(configured_output, DEFAULT_OUTPUT_TOKENS)
    return input_tokens, max(1, output_tokens)


def estimate_request_cost(
    request: ProviderRequest,
    price_table: PriceTable,
    *,
    safety_multiplier: Decimal = Decimal("1.20"),
) -> CostEstimate:
    input_tokens, output_tokens = estimate_request_tokens(request)
    model_pricing = _model_pricing(price_table, request.model)
    subtotal = (
        Decimal(input_tokens) * model_pricing.input_per_million_usd
        + Decimal(output_tokens) * model_pricing.output_per_million_usd
    ) / Decimal("1000000")
    return CostEstimate(estimated_usd=_money(subtotal * safety_multiplier))


def actual_response_cost(response: ProviderResponse, price_table: PriceTable) -> CostEstimate:
    model_pricing = _model_pricing(price_table, response.model)
    cached_tokens = min(response.usage.cached_input_tokens, response.usage.input_tokens)
    uncached_tokens = response.usage.input_tokens - cached_tokens
    output_tokens = response.usage.output_tokens + response.usage.reasoning_tokens
    subtotal = (
        Decimal(uncached_tokens) * model_pricing.input_per_million_usd
        + Decimal(cached_tokens) * model_pricing.cached_input_per_million_usd
        + Decimal(output_tokens) * model_pricing.output_per_million_usd
    ) / Decimal("1000000")
    return CostEstimate(estimated_usd=_money(subtotal))


def _model_pricing(price_table: PriceTable, model: str) -> ModelPricing:
    try:
        return price_table.model_prices[model]
    except KeyError as exc:
        raise ProjectError(
            ErrorCode.CFG_INVALID,
            "Model is missing from price table",
            detail={"model": model},
        ) from exc


def _token_estimate(text: str) -> int:
    words = max(1, len(text.split()))
    chars = max(1, len(text))
    return max(words, (chars + 3) // 4)


def _int_or_default(value: object, default: int) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return default


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.000001"))
