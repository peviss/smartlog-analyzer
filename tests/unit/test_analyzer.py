"""LLM integration tests with simulated Pydantic AI models: valid response, unexpected
format, retries with validation feedback, excessive delay (timeout) and provider failures."""

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from pydantic_ai.exceptions import ModelHTTPError, ModelTimeoutError

from api.core.errors import LLMResponseFormatError, LLMTimeoutError
from api.llm.analyzer import LLMCallFailed, LogAnalyzer
from api.llm.prompt import DATA_END, DATA_START, SYSTEM_PROMPT, extract_payload
from tests.fakes import VALID_RESPONSE, FakeModel, Text, valid_model


@dataclass
class FakeLog:
    id: int
    service_name: str
    level: str
    message: str
    timestamp: datetime = datetime(2026, 11, 16, tzinfo=UTC)


LOGS = [FakeLog(i, "payments-api", "error", f"DB pool exhausted, waited {i}ms") for i in range(1, 6)] + [
    FakeLog(10, "orders-api", "info", "Order 77 created")
]


async def run(model, llm_timeout=1.0, logs=LOGS, output_retries=1):
    analyzer = LogAnalyzer(model, timeout_seconds=llm_timeout, output_retries=output_retries)
    prepared = analyzer.prepare(logs)
    return prepared, await analyzer.run(prepared, allowed_log_ids={log.id for log in logs})


def mutated(mutation) -> dict:
    data = json.loads(json.dumps(VALID_RESPONSE))
    mutation(data)
    return data


async def test_valid_response_is_parsed():
    model = valid_model()
    _, outcome = await run(model)
    assert outcome.result.categories[0].name == "Database connectivity"
    assert json.loads(outcome.raw_response) == json.loads(model.text)
    assert outcome.duration_ms >= 0
    assert outcome.usage.requests == 1


async def test_prompt_contains_patterns_not_raw_duplicates():
    model = valid_model()
    await run(model)
    instructions, user = model.calls[0]
    assert instructions == SYSTEM_PROMPT
    assert DATA_START in user and DATA_END in user
    payload = extract_payload(user)
    assert payload["stats"]["total"] == 6
    error_pattern = payload["patterns"][0]
    assert error_pattern["template"] == "DB pool exhausted, waited <NUM>"
    assert error_pattern["count"] == 5
    assert len(payload["patterns"]) == 2


async def test_prompt_notes_truncated_patterns():
    logs = [FakeLog(i, "svc", "error", f"error kind {chr(65 + i)}") for i in range(10)]
    analyzer = LogAnalyzer(valid_model(), timeout_seconds=1, max_patterns=3)
    prepared = analyzer.prepare(logs)
    assert len(prepared.patterns) == 3
    assert prepared.truncated == 7
    assert "7 lower-priority patterns were omitted" in prepared.user_prompt


async def test_accepts_no_problems_found():
    _, outcome = await run(FakeModel({"summary": "All healthy.", "categories": [], "suggestions": []}))
    assert outcome.result.categories == [] and outcome.result.suggestions == []


async def test_ignores_extra_fields_from_the_model():
    _, outcome = await run(FakeModel({**VALID_RESPONSE, "confidence": 0.9}))
    assert outcome.result.summary


async def test_drops_log_ids_that_were_not_part_of_the_analysis():
    data = mutated(lambda d: d["categories"][0].update(example_log_ids=[1, 2, 999]))
    _, outcome = await run(FakeModel(data))
    assert outcome.result.categories[0].example_log_ids == [1, 2]


# ------------------------------------------------------------------ invalid output


async def test_plain_text_instead_of_structured_output_fails_and_keeps_raw_response():
    raw = "Sorry, I can only answer questions about cooking."
    with pytest.raises(LLMCallFailed) as exc_info:
        await run(FakeModel(Text(raw)))
    assert isinstance(exc_info.value.error, LLMResponseFormatError)
    assert exc_info.value.error.status_code == 502
    assert raw in exc_info.value.raw_response


@pytest.mark.parametrize(
    ("mutation", "fragment"),
    [
        (lambda d: d.pop("summary"), "summary"),
        (lambda d: d.update(summary=""), "summary"),
        (lambda d: d.update(categories="many"), "categories"),
        (lambda d: d["categories"][0].update(severity="apocalyptic"), "severity"),
        (lambda d: d["categories"][0].update(occurrences=-1), "occurrences"),
        (lambda d: d["suggestions"][0].update(priority="urgent"), "priority"),
        (lambda d: d["suggestions"][0].pop("title"), "title"),
    ],
)
async def test_schema_mismatch_is_a_format_error(mutation, fragment):
    with pytest.raises(LLMCallFailed) as exc_info:
        await run(FakeModel(mutated(mutation)))
    error = exc_info.value.error
    assert error.code == "LLM_INVALID_RESPONSE"
    assert fragment in error.message


@pytest.mark.parametrize("raw", ['{"summary": "truncated...", "categories": [', "[1, 2, 3]"])
async def test_malformed_tool_arguments_are_a_format_error(raw):
    with pytest.raises(LLMCallFailed) as exc_info:
        await run(FakeModel(raw))
    assert exc_info.value.error.code == "LLM_INVALID_RESPONSE"
    assert exc_info.value.raw_response


async def test_validation_errors_are_sent_back_and_the_model_can_fix_them():
    model = FakeModel({"summary": "ok"}, VALID_RESPONSE)
    _, outcome = await run(model, output_retries=1)
    assert outcome.result.categories[0].name == "Database connectivity"
    assert len(model.calls) == 2
    assert outcome.usage.requests == 2


async def test_no_retry_when_output_retries_is_zero():
    model = FakeModel({"summary": "ok"}, VALID_RESPONSE)
    with pytest.raises(LLMCallFailed):
        await run(model, output_retries=0)
    assert len(model.calls) == 1


# ------------------------------------------------------------------ timeouts and provider failures


async def test_excessive_delay_times_out_quickly():
    started = time.perf_counter()
    with pytest.raises(LLMCallFailed) as exc_info:
        await run(FakeModel(delay=5), llm_timeout=0.1)
    elapsed = time.perf_counter() - started
    assert isinstance(exc_info.value.error, LLMTimeoutError)
    assert exc_info.value.error.status_code == 504
    assert elapsed < 1, "the call must be cancelled at the deadline, not after the model finishes"
    assert 100 <= exc_info.value.duration_ms < 1000


async def test_response_just_within_deadline_succeeds():
    _, outcome = await run(FakeModel(delay=0.05), llm_timeout=1)
    assert outcome.result.summary


async def test_provider_timeout_maps_to_llm_timeout():
    with pytest.raises(LLMCallFailed) as exc_info:
        await run(FakeModel(ModelTimeoutError("fake-model", "read timed out")))
    assert exc_info.value.error.code == "LLM_TIMEOUT"


async def test_provider_http_error_is_mapped():
    with pytest.raises(LLMCallFailed) as exc_info:
        await run(FakeModel(ModelHTTPError(529, "fake-model")))
    error = exc_info.value.error
    assert error.code == "LLM_PROVIDER_ERROR"
    assert "529" in error.message


async def test_unexpected_provider_exception_is_wrapped_not_leaked():
    with pytest.raises(LLMCallFailed) as exc_info:
        await run(FakeModel(ConnectionResetError("socket closed")))
    error = exc_info.value.error
    assert error.code == "LLM_PROVIDER_ERROR"
    assert "ConnectionResetError" in error.message
