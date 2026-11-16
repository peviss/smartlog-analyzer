"""Pick the Pydantic AI model from configuration.

The rest of the app depends only on `pydantic_ai.models.Model`, so each provider is one
builder here. Pydantic AI implements the API calls; provider SDKs are imported lazily so
only the one in use needs to be installed.
"""

import inspect
from collections.abc import Callable

import httpx2
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings

from api.core.config import Settings

OLLAMA_DEFAULT_BASE_URL = "http://localhost:11434/v1"


def _api_key(settings: Settings, *, required: bool) -> str | None:
    key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else ""
    if required and not key:
        raise RuntimeError(f"LLM_PROVIDER={settings.llm_provider} requires LLM_API_KEY to be set.")
    return key or None


def _model_settings(settings: Settings) -> ModelSettings:
    return ModelSettings(max_tokens=settings.llm_max_tokens, temperature=0)


# The SDK timeouts are a second safety net; the analyzer enforces the overall deadline.


def _anthropic(settings: Settings, http_client: httpx2.AsyncClient | None) -> Model:
    import anthropic
    from pydantic_ai.models.anthropic import AnthropicModel
    from pydantic_ai.providers.anthropic import AnthropicProvider

    client = anthropic.AsyncAnthropic(
        api_key=_api_key(settings, required=True),
        base_url=settings.llm_base_url,
        timeout=settings.llm_timeout_seconds,
        max_retries=1,
        http_client=http_client,
    )
    return AnthropicModel(
        settings.llm_model,
        provider=AnthropicProvider(anthropic_client=client),
        settings=_model_settings(settings),
    )


def _openai_client(settings: Settings, http_client: httpx2.AsyncClient | None, *, api_key: str, base_url):
    import openai

    return openai.AsyncOpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=settings.llm_timeout_seconds,
        max_retries=1,
        http_client=http_client,
    )


def _openai(settings: Settings, http_client: httpx2.AsyncClient | None) -> Model:
    """OpenAI, or any OpenAI-compatible API (Azure, vLLM, LiteLLM...) through LLM_BASE_URL."""
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    client = _openai_client(
        settings, http_client, api_key=_api_key(settings, required=True), base_url=settings.llm_base_url
    )
    return OpenAIChatModel(
        settings.llm_model,
        provider=OpenAIProvider(openai_client=client),
        settings=_model_settings(settings),
    )


def _ollama(settings: Settings, http_client: httpx2.AsyncClient | None) -> Model:
    """Local models through Ollama's OpenAI-compatible API. No API key needed."""
    from pydantic_ai.models.ollama import OllamaModel
    from pydantic_ai.providers.ollama import OllamaProvider

    client = _openai_client(
        settings,
        http_client,
        # The SDK refuses an empty key; a local Ollama ignores it.
        api_key=_api_key(settings, required=False) or "ollama",
        base_url=settings.llm_base_url or OLLAMA_DEFAULT_BASE_URL,
    )
    return OllamaModel(
        settings.llm_model,
        provider=OllamaProvider(openai_client=client),
        settings=_model_settings(settings),
    )


def _google(settings: Settings, http_client: httpx2.AsyncClient | None) -> Model:
    """Gemini through the Gemini Developer API."""
    from pydantic_ai.models.google import GoogleModel
    from pydantic_ai.providers.google import GoogleProvider

    # The Google provider takes its request timeout from the HTTP client.
    http_client = http_client or httpx2.AsyncClient(
        timeout=httpx2.Timeout(settings.llm_timeout_seconds, connect=5)
    )
    provider = GoogleProvider(
        api_key=_api_key(settings, required=True), http_client=http_client, base_url=settings.llm_base_url
    )
    return GoogleModel(settings.llm_model, provider=provider, settings=_model_settings(settings))


_BUILDERS: dict[str, Callable[[Settings, httpx2.AsyncClient | None], Model]] = {
    "anthropic": _anthropic,
    "openai": _openai,
    "google": _google,
    "ollama": _ollama,
}


def build_model(settings: Settings, http_client: httpx2.AsyncClient | None = None) -> Model:
    builder = _BUILDERS.get(settings.llm_provider)
    if builder is None:
        from api.llm.mock import MockModel

        return MockModel(latency_ms=settings.mock_llm_latency_ms)
    try:
        return builder(settings, http_client)
    except ImportError as exc:
        raise RuntimeError(
            f"LLM_PROVIDER={settings.llm_provider} needs its SDK installed: {exc}. "
            'Install it with: pip install "pydantic-ai-slim[openai,google]"'
        ) from exc


def provider_name(model: Model) -> str:
    """Short provider name stored with each analysis and shown in /api/health."""
    return model.system


async def close_model(model: Model) -> None:
    client = getattr(model, "client", None)
    # google-genai keeps the async API under `client.aio`; its sync `close()` does not close it.
    aio = getattr(client, "aio", None)
    if aio is not None and hasattr(aio, "aclose"):
        await aio.aclose()
    close = getattr(client, "close", None)
    if close is not None:
        result = close()
        if inspect.isawaitable(result):
            await result
