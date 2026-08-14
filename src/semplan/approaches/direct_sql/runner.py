"""A1 direct-SQL baseline orchestration and guarded execution."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.exc import DBAPIError

from semplan.catalog.models import Catalog
from semplan.contracts import (
    Approach,
    BenchmarkCase,
    CanonicalResponse,
    DirectSqlEnvelope,
    LocalizedText,
    OutOfScopeResponse,
    ProviderFinishStatus,
    ProviderResponse,
    ResultOutcome,
    ScalarValue,
)
from semplan.data_generation.writer import canonical_json
from semplan.db import readonly_database_url
from semplan.errors import ErrorCode, ProjectError
from semplan.evaluation import canonicalize_row
from semplan.executor import CompiledSemanticQuery
from semplan.executor.sql_guard import validate_select_sql
from semplan.prompts import PromptRegistry
from semplan.providers import ModelProvider, build_provider_request
from semplan.reporting import response_from_out_of_scope


@dataclass(frozen=True)
class DirectSqlExecutionResult:
    outcome: ResultOutcome
    rows: list[dict[str, ScalarValue]]
    units: dict[str, str]
    compiled_query: CompiledSemanticQuery
    row_count: int


@dataclass(frozen=True)
class DirectSqlRunResult:
    approach: Approach
    case_id: str
    outcome: ResultOutcome
    provider_response: ProviderResponse
    direct_sql: DirectSqlEnvelope
    execution: DirectSqlExecutionResult | None
    response: CanonicalResponse


class DirectSqlRunner:
    """Run A1 through provider, direct SQL validation, read-only execution, and rendering."""

    def __init__(
        self,
        *,
        provider: ModelProvider,
        catalog: Catalog,
        prompt_registry: PromptRegistry,
        database_url: str | None = None,
        provider_name: str = "fake",
        model_name: str = "fake-direct-sql-v1",
        inference_parameters: dict[str, object] | None = None,
        request_metadata: dict[str, str] | None = None,
    ) -> None:
        self.provider = provider
        self.catalog = catalog
        self.prompt = prompt_registry.for_approach(Approach.A1)
        self.database_url = database_url
        self.provider_name = provider_name
        self.model_name = model_name
        self.inference_parameters = dict(inference_parameters or {"temperature": "0"})
        self.request_metadata = dict(request_metadata or {})

    def run_case(self, case: BenchmarkCase) -> DirectSqlRunResult:
        prompt_text = self.prompt.render(
            {
                "locale": case.language.value,
                "reference_date": case.context.reference_date.isoformat(),
                "utterance": case.utterance,
                "catalog_summary": _catalog_summary(self.catalog),
            }
        )
        provider_request = build_provider_request(
            provider=self.provider_name,
            model=self.model_name,
            prompt_id=self.prompt.metadata.prompt_id,
            prompt_sha256=self.prompt.sha256,
            system=prompt_text,
            inputs=[case.utterance],
            output_schema_ref=self.prompt.metadata.expected_output_schema,
            output_schema_sha256=self.prompt.output_schema_sha256,
            inference_parameters=self.inference_parameters,
            timeout_seconds=30,
            metadata={
                "approach": Approach.A1.value,
                "case_id": case.case_id,
                "prompt_sha256": self.prompt.sha256,
                **self.request_metadata,
            },
        )
        provider_response = self.provider.complete(provider_request)
        if provider_response.finish_status is not ProviderFinishStatus.STOP:
            raise ProjectError(
                ErrorCode.OUTPUT_SCHEMA_INVALID,
                "Provider did not return a completed direct-SQL response",
                detail={"finish_status": provider_response.finish_status.value},
            )
        try:
            direct_sql = DirectSqlEnvelope.model_validate(provider_response.parsed_payload)
        except ValidationError as exc:
            raise ProjectError(
                ErrorCode.OUTPUT_SCHEMA_INVALID,
                "Provider direct-SQL payload failed schema validation",
                detail={"schema": "DirectSqlEnvelope", "error_count": len(exc.errors())},
            ) from exc
        if direct_sql.cannot_answer:
            assert direct_sql.reason_code is not None
            response = response_from_out_of_scope(
                OutOfScopeResponse(
                    reason_code=direct_sql.reason_code,
                    message=LocalizedText(
                        **{
                            "en-US": "The request cannot be answered safely by direct SQL.",
                            "pt-BR": (
                                "A solicitacao nao pode ser respondida com SQL direto com "
                                "seguranca."
                            ),
                        }
                    ),
                )
            )
            return DirectSqlRunResult(
                approach=Approach.A1,
                case_id=case.case_id,
                outcome=ResultOutcome.OUT_OF_SCOPE,
                provider_response=provider_response,
                direct_sql=direct_sql,
                execution=None,
                response=response,
            )

        assert direct_sql.sql is not None
        execution = execute_direct_sql(
            direct_sql.sql,
            database_url=self.database_url,
        )
        response = CanonicalResponse(
            schema_version="1.0",
            outcome=ResultOutcome.ANSWERED,
            rows=execution.rows,
            units={},
            assumptions=direct_sql.assumptions,
            message=LocalizedText(
                **{
                    "en-US": f"Returned {execution.row_count} canonical row(s).",
                    "pt-BR": f"Retornou {execution.row_count} linha(s) canonica(s).",
                }
            ),
        )
        return DirectSqlRunResult(
            approach=Approach.A1,
            case_id=case.case_id,
            outcome=ResultOutcome.ANSWERED,
            provider_response=provider_response,
            direct_sql=direct_sql,
            execution=execution,
            response=response,
        )


def execute_direct_sql(
    sql: str,
    *,
    database_url: str | None = None,
    row_cap: int = 1000,
    statement_timeout_ms: int = 5000,
) -> DirectSqlExecutionResult:
    guarded = validate_select_sql(sql)
    executable_sql = guarded.sql.rstrip(";")
    engine = create_engine(database_url or readonly_database_url())
    try:
        with engine.connect() as connection:
            with connection.begin():
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                connection.exec_driver_sql(
                    f"SET LOCAL statement_timeout = '{statement_timeout_ms}ms'"
                )
                result = connection.execute(sa.text(executable_sql))
                rows = [
                    canonicalize_row(dict(row), {})
                    for row in result.mappings().fetchmany(row_cap + 1)
                ]
    except DBAPIError as exc:
        if "statement timeout" in str(exc).lower():
            raise ProjectError(
                ErrorCode.EXECUTION_TIMEOUT, "Direct SQL execution timed out"
            ) from exc
        raise ProjectError(
            ErrorCode.EXECUTION_FAILED,
            "Direct SQL execution failed",
            detail={"error_type": type(exc).__name__},
        ) from exc
    finally:
        engine.dispose()

    if len(rows) > row_cap:
        raise ProjectError(
            ErrorCode.EXECUTION_ROW_LIMIT,
            "Direct SQL returned more rows than allowed",
            detail={"rows": len(rows), "row_cap": row_cap},
        )
    sql_sha256 = (
        "sha256:"
        + hashlib.sha256(
            canonical_json({"sql": guarded.normalized_sql}).encode("utf-8")
        ).hexdigest()
    )
    return DirectSqlExecutionResult(
        outcome=ResultOutcome.ANSWERED,
        rows=rows,
        units={},
        compiled_query=CompiledSemanticQuery(
            sql=executable_sql,
            guard_sql=guarded.normalized_sql,
            bind_params={},
            sql_sha256=sql_sha256,
            statement=None,
        ),
        row_count=len(rows),
    )


def _catalog_summary(catalog: Catalog) -> str:
    metrics = "\n".join(
        f"- {metric_id}: {metric.labels.en_us} ({metric.sql.view}.{metric.sql.expression})"
        for metric_id, metric in sorted(catalog.metrics.items())
    )
    dimensions = "\n".join(
        f"- {dimension_id}: {dimension.labels.en_us} "
        f"({dimension.source.view}.{dimension.source.column})"
        for dimension_id, dimension in sorted(catalog.dimensions.items())
    )
    return f"Metrics:\n{metrics}\nDimensions:\n{dimensions}"
