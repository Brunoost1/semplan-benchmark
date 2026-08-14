"""Fail-closed budget controller and local spend ledger."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from semplan.contracts import (
    BudgetCheck,
    PriceTable,
    ProviderRequest,
    ProviderResponse,
    RunManifest,
)
from semplan.costs.pricing import (
    actual_response_cost,
    ensure_price_table_fresh,
    estimate_request_cost,
    estimate_request_tokens,
)
from semplan.data_generation.writer import canonical_json
from semplan.errors import ErrorCode, ProjectError

MONTHLY_LIMIT_USD = Decimal("20.00")


class BudgetLedger:
    """Small JSON ledger used to enforce the monthly external-service ceiling."""

    def __init__(self, path: Path, *, monthly_limit_usd: Decimal = MONTHLY_LIMIT_USD) -> None:
        self.path = path
        self.monthly_limit_usd = monthly_limit_usd

    def total_spend_usd(self) -> Decimal:
        payload = self._payload()
        entries = cast(list[dict[str, object]], payload["entries"])
        return sum((Decimal(str(entry["cost_usd"])) for entry in entries), Decimal("0"))

    def append_response(self, response: ProviderResponse, cost_usd: Decimal) -> None:
        current_total = self.total_spend_usd()
        if current_total + cost_usd > self.monthly_limit_usd:
            raise ProjectError(
                ErrorCode.BUDGET_EXCEEDED,
                "Monthly ledger limit would be exceeded",
                detail={
                    "current_spend_usd": str(current_total),
                    "new_cost_usd": str(cost_usd),
                    "monthly_limit_usd": str(self.monthly_limit_usd),
                },
            )
        payload = self._payload()
        entries = cast(list[dict[str, object]], payload["entries"])
        entries.append(
            {
                "recorded_at_utc": datetime.now(UTC).isoformat(),
                "provider": response.provider,
                "model": response.model,
                "response_id": response.response_id,
                "cost_usd": str(cost_usd),
            }
        )
        self._write(payload)

    def _payload(self) -> dict[str, Any]:
        if not self.path.exists():
            return {
                "schema_version": "1.0",
                "currency": "USD",
                "monthly_limit_usd": str(self.monthly_limit_usd),
                "entries": [],
            }
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProjectError(
                ErrorCode.CFG_INVALID,
                "Budget ledger cannot be loaded",
                detail={"path": str(self.path), "reason": str(exc)},
            ) from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("entries"), list):
            raise ProjectError(ErrorCode.CFG_INVALID, "Budget ledger is malformed")
        return payload

    def _write(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(canonical_json(payload) + "\n", encoding="utf-8", newline="\n")
        tmp_path.replace(self.path)


@dataclass
class BudgetController:
    run_manifest: RunManifest
    price_table: PriceTable
    allow_paid: bool
    api_key_present: bool
    ledger: BudgetLedger | None = None
    safety_multiplier: Decimal = Decimal("1.20")
    monthly_limit_usd: Decimal = MONTHLY_LIMIT_USD
    max_price_age_days: int = 7
    current_run_spend_usd: Decimal = Decimal("0")

    def preflight(self, request: ProviderRequest, *, now: datetime | None = None) -> BudgetCheck:
        if not self.allow_paid:
            raise ProjectError(ErrorCode.BUDGET_EXCEEDED, "Paid execution requires --allow-paid")
        if not self.run_manifest.allow_paid:
            raise ProjectError(
                ErrorCode.BUDGET_EXCEEDED,
                "Run manifest does not permit paid execution",
            )
        if not self.api_key_present:
            raise ProjectError(ErrorCode.CFG_INVALID, "OPENAI_API_KEY is required for paid runs")
        if self.run_manifest.budget_usd <= Decimal("0"):
            raise ProjectError(ErrorCode.BUDGET_EXCEEDED, "Run manifest budget must be positive")
        ensure_price_table_fresh(
            self.price_table,
            now=now,
            max_age_days=self.max_price_age_days,
        )

        input_tokens, output_tokens = estimate_request_tokens(request)
        estimate = estimate_request_cost(
            request,
            self.price_table,
            safety_multiplier=self.safety_multiplier,
        )
        monthly_spend = self.ledger.total_spend_usd() if self.ledger else Decimal("0")
        remaining_run = self.run_manifest.budget_usd - self.current_run_spend_usd
        remaining_monthly = self.monthly_limit_usd - monthly_spend
        if estimate.estimated_usd > remaining_run:
            raise ProjectError(
                ErrorCode.BUDGET_EXCEEDED,
                "Preflight estimate exceeds remaining run budget",
                detail={
                    "estimate_usd": str(estimate.estimated_usd),
                    "remaining_run_budget_usd": str(remaining_run),
                },
            )
        if estimate.estimated_usd > remaining_monthly:
            raise ProjectError(
                ErrorCode.BUDGET_EXCEEDED,
                "Preflight estimate exceeds remaining monthly budget",
                detail={
                    "estimate_usd": str(estimate.estimated_usd),
                    "remaining_monthly_budget_usd": str(remaining_monthly),
                },
            )

        return BudgetCheck(
            schema_version="1.0",
            request_hash=request.idempotency_hash,
            model=request.model,
            estimated_input_tokens=input_tokens,
            estimated_output_tokens=output_tokens,
            safety_multiplier=self.safety_multiplier,
            estimated_usd=estimate.estimated_usd,
            run_budget_usd=self.run_manifest.budget_usd,
            monthly_limit_usd=self.monthly_limit_usd,
            remaining_run_budget_usd=remaining_run,
            remaining_monthly_budget_usd=remaining_monthly,
            price_checked_at_utc=self.price_table.checked_at_utc,
        )

    def record_response(self, response: ProviderResponse) -> Decimal:
        actual = actual_response_cost(response, self.price_table).estimated_usd
        if self.current_run_spend_usd + actual > self.run_manifest.budget_usd:
            raise ProjectError(
                ErrorCode.BUDGET_EXCEEDED,
                "Actual spend exceeds run budget",
                detail={
                    "current_run_spend_usd": str(self.current_run_spend_usd),
                    "actual_cost_usd": str(actual),
                    "run_budget_usd": str(self.run_manifest.budget_usd),
                },
            )
        if self.ledger is not None:
            self.ledger.append_response(response, actual)
        self.current_run_spend_usd += actual
        return actual
