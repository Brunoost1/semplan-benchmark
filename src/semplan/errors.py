"""Stable project error records and exceptions."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ErrorCode(StrEnum):
    """Normative error codes from docs/012_error_model.md."""

    CFG_INVALID = "CFG_INVALID"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    PROVIDER_RATE_LIMIT = "PROVIDER_RATE_LIMIT"
    PROVIDER_REFUSAL = "PROVIDER_REFUSAL"
    OUTPUT_SCHEMA_INVALID = "OUTPUT_SCHEMA_INVALID"
    SQL_PARSE_FAILED = "SQL_PARSE_FAILED"
    POLICY_VIOLATION = "POLICY_VIOLATION"
    CATALOG_UNKNOWN_ID = "CATALOG_UNKNOWN_ID"
    AMBIGUOUS_REQUIRED = "AMBIGUOUS_REQUIRED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    EXECUTION_TIMEOUT = "EXECUTION_TIMEOUT"
    EXECUTION_ROW_LIMIT = "EXECUTION_ROW_LIMIT"
    GOLD_INVALID = "GOLD_INVALID"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"


ERROR_LAYERS: dict[ErrorCode, str] = {
    ErrorCode.CFG_INVALID: "config",
    ErrorCode.PROVIDER_TIMEOUT: "provider",
    ErrorCode.PROVIDER_RATE_LIMIT: "provider",
    ErrorCode.PROVIDER_REFUSAL: "provider",
    ErrorCode.OUTPUT_SCHEMA_INVALID: "approach",
    ErrorCode.SQL_PARSE_FAILED: "A1",
    ErrorCode.POLICY_VIOLATION: "guard",
    ErrorCode.CATALOG_UNKNOWN_ID: "normalizer",
    ErrorCode.AMBIGUOUS_REQUIRED: "normalizer",
    ErrorCode.EXECUTION_FAILED: "executor",
    ErrorCode.EXECUTION_TIMEOUT: "executor",
    ErrorCode.EXECUTION_ROW_LIMIT: "executor",
    ErrorCode.GOLD_INVALID: "evaluator",
    ErrorCode.BUDGET_EXCEEDED: "cost",
}


class ProjectErrorRecord(BaseModel):
    """Machine-readable error payload used across project layers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: ErrorCode
    message: str = Field(min_length=1)
    layer: str = Field(min_length=1)
    retryable: bool
    case_id: str | None = None
    run_id: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class ProjectError(Exception):
    """Base exception carrying one stable, redacted error record."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        retryable: bool = False,
        case_id: str | None = None,
        run_id: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.record = ProjectErrorRecord(
            code=code,
            message=message,
            layer=ERROR_LAYERS[code],
            retryable=retryable,
            case_id=case_id,
            run_id=run_id,
            detail=detail or {},
        )
        super().__init__(message)

    def to_record(self) -> ProjectErrorRecord:
        """Return the immutable machine-readable error record."""

        return self.record
