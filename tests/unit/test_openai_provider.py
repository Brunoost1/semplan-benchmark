from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from semplan.contracts import ModelPricing, PriceTable, ProviderRequest
from semplan.errors import ProjectError
from semplan.providers import OpenAIProvider


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
                cached_input_per_million_usd=Decimal("0.1"),
            )
        },
    )


def _request() -> ProviderRequest:
    return ProviderRequest(
        schema_version="1.0",
        provider="openai",
        model="gpt-5.6-luna",
        prompt_id="direct_sql_a1_v1",
        prompt_sha256="sha256:" + ("a" * 64),
        system="system",
        inputs=["input"],
        output_schema_ref="direct_sql.schema.json",
        output_schema_sha256="sha256:" + ("b" * 64),
        inference_parameters={"temperature": "0", "reasoning_effort": "low"},
        timeout_seconds=30,
        metadata={},
        idempotency_hash="sha256:" + ("c" * 64),
    )


class FakeResponses:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.calls = 0
        self.last_request: dict[str, object] | None = None

    def create(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls += 1
        self.last_request = kwargs
        return self.payload


class FakeClient:
    def __init__(self, payload: dict[str, object]) -> None:
        self.responses = FakeResponses(payload)


def test_openai_payload_uses_strict_structured_outputs() -> None:
    provider = OpenAIProvider(price_table=_price_table(), client=FakeClient({}))

    payload = provider.build_responses_payload(_request())

    assert payload["model"] == "gpt-5.6-luna"
    assert payload["reasoning"] == {"effort": "low"}
    assert "reasoning_effort" not in payload
    assert payload["text"]["format"]["strict"] is True  # type: ignore[index]
    assert payload["text"]["format"]["schema"]["additionalProperties"] is False  # type: ignore[index]
    schema = payload["text"]["format"]["schema"]  # type: ignore[index]
    assert set(schema["required"]) == set(schema["properties"])  # type: ignore[index]
    serialized_schema = json.dumps(schema)
    assert "default" not in serialized_schema
    assert "pattern" not in serialized_schema
    assert provider.estimate_cost(_request()).estimated_usd > Decimal("0")
    assert provider.healthcheck().status == "OK"


def test_openai_payload_converts_tool_agent_discriminated_union_for_structured_outputs() -> None:
    request = _request().model_copy(
        update={
            "prompt_id": "tool_agent_a2_v1",
            "output_schema_ref": "tool_agent_turn.schema.json",
        }
    )
    provider = OpenAIProvider(price_table=_price_table(), client=FakeClient({}))

    payload = provider.build_responses_payload(request)
    schema = payload["text"]["format"]["schema"]  # type: ignore[index]
    serialized_schema = json.dumps(schema)

    assert "discriminator" not in serialized_schema
    assert "oneOf" not in serialized_schema
    assert "anyOf" in serialized_schema
    assert schema["properties"]["tool_calls"]["items"]["anyOf"]  # type: ignore[index]


def test_openai_provider_refuses_without_allow_paid_before_client_call() -> None:
    client = FakeClient({})
    provider = OpenAIProvider(price_table=_price_table(), client=client, allow_paid=False)

    with pytest.raises(ProjectError):
        provider.complete(_request())

    assert client.responses.calls == 0


def test_openai_provider_parses_completed_response_and_usage() -> None:
    client = FakeClient(
        {
            "id": "resp-1",
            "model": "gpt-5.6-luna",
            "status": "completed",
            "output_text": json.dumps(
                {
                    "schema_version": "1.0",
                    "sql": "SELECT 1",
                    "assumptions": [],
                    "cannot_answer": False,
                    "reason_code": None,
                }
            ),
            "usage": {
                "input_tokens": 10,
                "output_tokens": 20,
                "input_tokens_details": {"cached_tokens": 3},
                "output_tokens_details": {"reasoning_tokens": 2},
            },
        }
    )
    provider = OpenAIProvider(price_table=_price_table(), client=client, allow_paid=True)

    response = provider.complete(_request())

    assert response.response_id == "resp-1"
    assert response.parsed_payload is not None
    assert response.usage.cached_input_tokens == 3
    assert response.cost.estimated_usd > Decimal("0")


def test_openai_provider_preserves_output_text_property_from_sdk_response() -> None:
    class ResponseObject:
        output_text = json.dumps(
            {
                "schema_version": "1.0",
                "sql": "SELECT 1",
                "assumptions": [],
                "cannot_answer": False,
                "reason_code": None,
            }
        )

        def model_dump(self, mode: str):  # type: ignore[no-untyped-def]
            assert mode == "json"
            return {
                "id": "resp-property",
                "model": "gpt-5.6-luna",
                "status": "completed",
                "usage": {"input_tokens": 10, "output_tokens": 20},
            }

    provider = OpenAIProvider(
        price_table=_price_table(),
        client=FakeClient(ResponseObject()),
        allow_paid=True,
    )

    response = provider.complete(_request())

    assert response.response_id == "resp-property"
    assert response.parsed_payload is not None
    assert response.raw_payload["output_text"] == ResponseObject.output_text


def test_openai_provider_handles_refusal_and_incomplete() -> None:
    refusal_provider = OpenAIProvider(
        price_table=_price_table(),
        client=FakeClient(
            {
                "id": "resp-refusal",
                "model": "gpt-5.6-luna",
                "status": "completed",
                "refusal": "Cannot comply.",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        ),
        allow_paid=True,
    )
    incomplete_provider = OpenAIProvider(
        price_table=_price_table(),
        client=FakeClient(
            {
                "id": "resp-incomplete",
                "model": "gpt-5.6-luna",
                "status": "incomplete",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        ),
        allow_paid=True,
    )

    assert refusal_provider.complete(_request()).finish_status == "REFUSAL"
    assert incomplete_provider.complete(_request()).finish_status == "INCOMPLETE"


def test_openai_provider_healthcheck_reports_missing_key() -> None:
    provider = OpenAIProvider(price_table=_price_table())

    health = provider.healthcheck()

    assert health.status == "UNAVAILABLE"


def test_openai_provider_rejects_missing_schema(tmp_path) -> None:  # type: ignore[no-untyped-def]
    provider = OpenAIProvider(price_table=_price_table(), schema_root=tmp_path)

    with pytest.raises(ProjectError):
        provider.build_responses_payload(_request())


def test_openai_provider_rejects_non_object_schema(tmp_path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "direct_sql.schema.json").write_text("[]\n", encoding="utf-8")
    provider = OpenAIProvider(price_table=_price_table(), schema_root=tmp_path)

    with pytest.raises(ProjectError):
        provider.build_responses_payload(_request())


def test_openai_provider_preserves_invalid_output_json_as_error_response() -> None:
    provider = OpenAIProvider(
        price_table=_price_table(),
        client=FakeClient(
            {
                "id": "resp-invalid",
                "model": "gpt-5.6-luna",
                "status": "completed",
                "output_text": "{not-json",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        ),
        allow_paid=True,
    )

    response = provider.complete(_request())

    assert response.finish_status == "ERROR"
    assert response.parsed_payload is None
    assert response.raw_payload["output_text"] == "{not-json"
    assert "_semplan_parse_error" in response.raw_payload
    assert response.cost.estimated_usd > Decimal("0")


def test_openai_provider_preserves_non_object_output_json_as_error_response() -> None:
    provider = OpenAIProvider(
        price_table=_price_table(),
        client=FakeClient(
            {
                "id": "resp-list",
                "model": "gpt-5.6-luna",
                "status": "completed",
                "output_text": "[]",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        ),
        allow_paid=True,
    )

    response = provider.complete(_request())

    assert response.finish_status == "ERROR"
    assert response.parsed_payload is None
    assert response.raw_payload["output_text"] == "[]"
    assert "_semplan_parse_error" in response.raw_payload
    assert response.cost.estimated_usd > Decimal("0")


def test_openai_provider_maps_timeout_to_retryable_error() -> None:
    class TimeoutResponses:
        def create(self, **_kwargs):  # type: ignore[no-untyped-def]
            raise TimeoutError

    class TimeoutClient:
        responses = TimeoutResponses()

    provider = OpenAIProvider(price_table=_price_table(), client=TimeoutClient(), allow_paid=True)

    with pytest.raises(ProjectError) as exc_info:
        provider.complete(_request())

    assert exc_info.value.to_record().retryable is True
    assert exc_info.value.to_record().detail["error_type"] == "TimeoutError"


def test_openai_provider_maps_generic_sdk_error() -> None:
    class SyntheticSDKError(ValueError):
        status_code = 429
        request_id = "req-test"
        code = "rate_limit_exceeded"
        type = "rate_limit_error"

    class ErrorResponses:
        def create(self, **_kwargs):  # type: ignore[no-untyped-def]
            raise SyntheticSDKError("sdk")

    class ErrorClient:
        responses = ErrorResponses()

    provider = OpenAIProvider(price_table=_price_table(), client=ErrorClient(), allow_paid=True)

    with pytest.raises(ProjectError) as exc_info:
        provider.complete(_request())

    assert exc_info.value.to_record().retryable is True
    assert exc_info.value.to_record().detail == {
        "error_type": "SyntheticSDKError",
        "provider_error_code": "rate_limit_exceeded",
        "provider_error_type": "rate_limit_error",
        "request_id": "req-test",
        "status_code": 429,
    }


def test_openai_provider_build_client_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    provider = OpenAIProvider(price_table=_price_table(), allow_paid=True)

    with pytest.raises(ProjectError):
        provider.complete(_request())


def test_openai_provider_accepts_parsed_payload_without_output_text() -> None:
    provider = OpenAIProvider(
        price_table=_price_table(),
        client=FakeClient(
            {
                "id": "resp-parsed",
                "model": "gpt-5.6-luna",
                "status": "completed",
                "parsed": {
                    "schema_version": "1.0",
                    "sql": "SELECT 1",
                    "assumptions": [],
                    "cannot_answer": False,
                    "reason_code": None,
                },
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }
        ),
        allow_paid=True,
    )

    assert provider.complete(_request()).parsed_payload is not None


def test_openai_provider_handles_output_list_refusal_and_missing_usage() -> None:
    provider = OpenAIProvider(
        price_table=_price_table(),
        client=FakeClient(
            {
                "id": "resp-output-refusal",
                "model": "gpt-5.6-luna",
                "status": "completed",
                "output": [{"refusal": "No."}],
            }
        ),
        allow_paid=True,
    )

    response = provider.complete(_request())

    assert response.finish_status == "REFUSAL"
    assert response.usage.input_tokens == 0
