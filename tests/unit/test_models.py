import json

import httpx2 as httpx  # the Anthropic SDK 2.x transport
import pytest
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.models.openai import OpenAIChatModel

from api.core.config import Settings
from api.llm.analyzer import LLMCallFailed, LogAnalyzer
from api.llm.mock import MockModel, build_mock_analysis
from api.llm.providers import build_model, close_model, provider_name
from tests.fakes import VALID_RESPONSE
from tests.unit.test_analyzer import LOGS, FakeLog


def _payload(patterns):
    return {
        "stats": {"total": 10, "by_level": {"error": 6}, "errors_by_service": {"payments": 6}},
        "patterns": patterns,
    }


async def test_mock_output_satisfies_the_llm_contract():
    logs = (
        [FakeLog(i, "payments", "error", "Database connection refused") for i in range(1, 26)]
        + [FakeLog(30 + i, "orders", "warn", f"Request took {i}ms (timeout)") for i in range(4)]
        + [FakeLog(40, "db", "critical", "Disk full")]
        + [FakeLog(50 + i, "orders", "info", "Order created") for i in range(10)]
    )
    analyzer = LogAnalyzer(MockModel(latency_ms=0), timeout_seconds=1)
    prepared = analyzer.prepare(logs)
    outcome = await analyzer.run(prepared, allowed_log_ids={log.id for log in logs})
    result = outcome.result
    names = [c.name for c in result.categories]
    assert names[0] == "Database connectivity"
    assert result.categories[0].severity == "critical"  # bumped: >= 20 occurrences of a high rule
    assert "Resource exhaustion" in names
    info_ids = set(range(50, 60))
    assert all(not info_ids & set(c.example_log_ids) for c in result.categories), "INFO noise is ignored"
    assert result.suggestions


def test_mock_reports_healthy_when_there_are_no_problems():
    result = build_mock_analysis(
        _payload([{"template": "ok", "level": "info", "count": 3, "services": {"a": 3}}])
    )
    assert result["categories"] == [] and result["suggestions"] == []
    assert "no warnings or errors" in result["summary"]


def test_factory_defaults_to_mock():
    model = build_model(Settings(llm_provider="mock"))
    assert isinstance(model, MockModel)
    assert provider_name(model) == "mock" and model.model_name == "mock-rules-v1"


def test_factory_requires_api_key_for_anthropic():
    with pytest.raises(RuntimeError, match="LLM_API_KEY"):
        build_model(Settings(llm_provider="anthropic", llm_api_key=None))


def test_factory_builds_anthropic_model():
    model = build_model(
        Settings(llm_provider="anthropic", llm_api_key="sk-test", llm_model="claude-x", llm_max_tokens=123)
    )
    assert isinstance(model, AnthropicModel)
    assert provider_name(model) == "anthropic" and model.model_name == "claude-x"
    assert model.settings == {"max_tokens": 123, "temperature": 0}


# ------------------------------------------------------------------ real AnthropicModel, fake HTTP


def _anthropic(handler) -> tuple[LogAnalyzer, list[dict]]:
    requests: list[dict] = []

    def record(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return handler(request)

    settings = Settings(llm_provider="anthropic", llm_api_key="sk-test", llm_model="claude-x")
    model = build_model(settings, http_client=httpx.AsyncClient(transport=httpx.MockTransport(record)))
    model.client.max_retries = 0
    return LogAnalyzer(model, timeout_seconds=5), requests


def _message(content: list[dict]) -> dict:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-x",
        "content": content,
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {"input_tokens": 100, "output_tokens": 50},
    }


async def test_anthropic_structured_output_round_trip():
    def handler(request: httpx.Request) -> httpx.Response:
        tool = json.loads(request.content)["tools"][0]["name"]
        data = {
            **VALID_RESPONSE,
            "categories": [{**VALID_RESPONSE["categories"][0], "example_log_ids": [1, 999]}],
        }
        return httpx.Response(
            200, json=_message([{"type": "tool_use", "id": "toolu_1", "name": tool, "input": data}])
        )

    analyzer, requests = _anthropic(handler)
    outcome = await analyzer.run(analyzer.prepare(LOGS), allowed_log_ids={log.id for log in LOGS})

    body = requests[0]
    assert body["model"] == "claude-x" and body["temperature"] == 0
    assert "Elastic Common Schema" in str(body["system"])
    schema = body["tools"][0]["input_schema"]
    assert set(schema["required"]) == {"summary", "categories", "suggestions"}
    assert outcome.result.categories[0].example_log_ids == [1]
    assert outcome.usage.input_tokens == 100


async def test_anthropic_http_error_maps_to_provider_error():
    analyzer, _ = _anthropic(
        lambda r: httpx.Response(429, json={"type": "error", "error": {"type": "rate_limit_error"}})
    )
    with pytest.raises(LLMCallFailed) as exc_info:
        await analyzer.run(analyzer.prepare(LOGS), allowed_log_ids=set())
    assert exc_info.value.error.code == "LLM_PROVIDER_ERROR"


async def test_anthropic_timeout_maps_to_llm_timeout():
    def handler(request):
        raise httpx.ReadTimeout("timed out", request=request)

    analyzer, _ = _anthropic(handler)
    with pytest.raises(LLMCallFailed) as exc_info:
        await analyzer.run(analyzer.prepare(LOGS), allowed_log_ids=set())
    assert exc_info.value.error.code == "LLM_TIMEOUT"


# ------------------------------------------------------------------ other providers


@pytest.mark.parametrize("provider", ["openai", "google"])
def test_factory_requires_api_key(provider):
    with pytest.raises(RuntimeError, match=f"LLM_PROVIDER={provider} requires LLM_API_KEY"):
        build_model(Settings(llm_provider=provider, llm_api_key=None))


@pytest.mark.parametrize(
    ("provider", "model_class", "base_url"),
    [
        ("openai", OpenAIChatModel, "https://api.openai.com/v1/"),
        ("google", GoogleModel, "https://generativelanguage.googleapis.com/"),
        ("ollama", OllamaModel, "http://localhost:11434/v1/"),
    ],
)
def test_factory_builds_provider_model(provider, model_class, base_url):
    model = build_model(
        Settings(llm_provider=provider, llm_api_key="key", llm_model="model-x", llm_max_tokens=123)
    )
    assert isinstance(model, model_class)
    assert provider_name(model) == provider and model.model_name == "model-x"
    assert model.base_url == base_url
    assert model.settings == {"max_tokens": 123, "temperature": 0}


def test_ollama_needs_no_api_key_and_accepts_a_base_url():
    model = build_model(
        Settings(llm_provider="ollama", llm_api_key=None, llm_base_url="http://ollama:11434/v1")
    )
    assert isinstance(model, OllamaModel) and model.base_url == "http://ollama:11434/v1/"


def test_openai_accepts_a_compatible_base_url():
    model = build_model(
        Settings(llm_provider="openai", llm_api_key="key", llm_base_url="https://llm.internal/v1")
    )
    assert model.base_url == "https://llm.internal/v1/"


async def test_close_model_handles_sync_and_async_clients():
    for provider in ("openai", "google", "ollama"):
        await close_model(build_model(Settings(llm_provider=provider, llm_api_key="key")))


def _openai(handler) -> tuple[LogAnalyzer, list[dict]]:
    requests: list[dict] = []

    def record(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return handler(request)

    settings = Settings(llm_provider="openai", llm_api_key="sk-test", llm_model="gpt-x")
    model = build_model(settings, http_client=httpx.AsyncClient(transport=httpx.MockTransport(record)))
    model.client.max_retries = 0
    return LogAnalyzer(model, timeout_seconds=5), requests


async def test_openai_structured_output_round_trip():
    def handler(request: httpx.Request) -> httpx.Response:
        tool = json.loads(request.content)["tools"][0]["function"]["name"]
        call = {
            "id": "call_1",
            "type": "function",
            "function": {"name": tool, "arguments": json.dumps(VALID_RESPONSE)},
        }
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "created": 0,
                "model": "gpt-x",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls",
                        "message": {"role": "assistant", "content": None, "tool_calls": [call]},
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            },
        )

    analyzer, requests = _openai(handler)
    outcome = await analyzer.run(analyzer.prepare(LOGS), allowed_log_ids={log.id for log in LOGS})

    body = requests[0]
    assert body["model"] == "gpt-x" and body["temperature"] == 0
    assert "Elastic Common Schema" in str(body["messages"][0])
    assert set(body["tools"][0]["function"]["parameters"]["required"]) == {
        "summary",
        "categories",
        "suggestions",
    }
    assert outcome.result.summary == VALID_RESPONSE["summary"]
    assert outcome.usage.input_tokens == 100


async def test_openai_http_error_maps_to_provider_error():
    analyzer, _ = _openai(lambda r: httpx.Response(429, json={"error": {"message": "rate limited"}}))
    with pytest.raises(LLMCallFailed) as exc_info:
        await analyzer.run(analyzer.prepare(LOGS), allowed_log_ids=set())
    assert exc_info.value.error.code == "LLM_PROVIDER_ERROR"
