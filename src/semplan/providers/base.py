"""Provider-neutral model interface used by benchmark approaches."""

from __future__ import annotations

import hashlib
from typing import Protocol

from semplan.contracts import (
    CostEstimate,
    ProviderHealth,
    ProviderRequest,
    ProviderResponse,
)
from semplan.data_generation.writer import canonical_json


class ModelProvider(Protocol):
    """Boundary implemented by fake, replay, and live providers."""

    def complete(self, request: ProviderRequest) -> ProviderResponse: ...

    def estimate_cost(self, request: ProviderRequest) -> CostEstimate: ...

    def healthcheck(self) -> ProviderHealth: ...


def provider_request_hash(payload: dict[str, object]) -> str:
    """Hash a provider request payload after excluding secrets and transport state."""

    sanitized = {key: value for key, value in payload.items() if key != "idempotency_hash"}
    digest = hashlib.sha256(canonical_json(sanitized).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def build_provider_request(
    *,
    provider: str,
    model: str,
    prompt_id: str,
    prompt_sha256: str,
    system: str,
    inputs: list[str],
    output_schema_ref: str,
    output_schema_sha256: str | None = None,
    inference_parameters: dict[str, object] | None = None,
    timeout_seconds: int = 30,
    metadata: dict[str, str] | None = None,
) -> ProviderRequest:
    """Create a canonical request with a deterministic idempotency hash."""

    payload: dict[str, object] = {
        "schema_version": "1.0",
        "provider": provider,
        "model": model,
        "prompt_id": prompt_id,
        "prompt_sha256": prompt_sha256,
        "system": system,
        "inputs": inputs,
        "output_schema_ref": output_schema_ref,
        "output_schema_sha256": output_schema_sha256,
        "inference_parameters": inference_parameters or {},
        "timeout_seconds": timeout_seconds,
        "metadata": metadata or {},
    }
    payload["idempotency_hash"] = provider_request_hash(payload)
    return ProviderRequest.model_validate(payload)
