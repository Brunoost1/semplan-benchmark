"""Strict Pydantic models for checked-in configuration."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Approach = Literal["A1", "A2", "A3", "A4"]


class StrictModel(BaseModel):
    """Base model that rejects unknown fields and avoids mutable instances."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ProjectConfig(StrictModel):
    reference_date: date
    timezone: str = Field(min_length=1)


class DataConfig(StrictModel):
    seed: int = Field(ge=0)
    profile: Literal["small", "full"]


class ProviderConfig(StrictModel):
    name: Literal["openai"]
    model: str = Field(min_length=1)
    reasoning_effort: Literal["low", "medium", "high"]
    max_output_tokens: int = Field(gt=0)
    timeout_seconds: int = Field(gt=0)


class ExperimentConfig(StrictModel):
    repetitions: int = Field(gt=0)
    approaches: Annotated[list[Approach], Field(min_length=1)]
    budget_usd: Decimal = Field(gt=Decimal("0"))
    allow_paid: bool


class ExecutionConfig(StrictModel):
    statement_timeout_ms: int = Field(gt=0)
    max_rows: int = Field(gt=0)
    read_only: bool


class RootConfig(StrictModel):
    schema_version: Literal["1.0"]
    project: ProjectConfig
    data: DataConfig
    provider: ProviderConfig
    experiment: ExperimentConfig
    execution: ExecutionConfig

    def canonical_json(self) -> str:
        """Return stable JSON for manifests and hashes."""

        payload = self.model_dump(mode="json")
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    def sha256(self) -> str:
        """Return a SHA-256 hash of the stable redacted configuration payload."""

        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()
