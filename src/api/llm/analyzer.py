"""Glue between the logs and the LLM: preprocess, run the agent with a deadline, map errors."""

import time
from dataclasses import dataclass

from pydantic import ValidationError
from pydantic_ai import ModelMessage, RunUsage, capture_run_messages
from pydantic_ai.exceptions import (
    ModelAPIError,
    ModelRequestTimeout,
    ModelTimeoutError,
    RunTimedOut,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models import Model

from api.core.errors import LLMError, LLMProviderError, LLMResponseFormatError, LLMTimeoutError
from api.domain.patterns import LogLike, compute_stats, extract_patterns
from api.llm.agent import AnalysisDeps, analysis_agent
from api.llm.prompt import build_user_prompt
from api.llm.providers import provider_name
from api.schemas.analysis import AnalysisResult


@dataclass
class PreparedInput:
    stats: dict
    patterns: list[dict]
    truncated: int
    user_prompt: str


@dataclass
class AnalyzerOutcome:
    result: AnalysisResult
    raw_response: str
    duration_ms: int
    usage: RunUsage


class LLMCallFailed(Exception):
    """Wraps an LLMError together with whatever we know about the attempt (for persistence)."""

    def __init__(self, error: LLMError, duration_ms: int, raw_response: str | None = None) -> None:
        super().__init__(error.message)
        self.error = error
        self.duration_ms = duration_ms
        self.raw_response = raw_response


def render_responses(messages: list[ModelMessage]) -> str | None:
    """What the model actually said (text and tool-call arguments), kept for debugging."""
    chunks = []
    for message in messages:
        if not isinstance(message, ModelResponse):
            continue
        for part in message.parts:
            if isinstance(part, TextPart) and part.content.strip():
                chunks.append(part.content)
            elif isinstance(part, ToolCallPart):
                chunks.append(part.args_as_json_str())
    return "\n\n".join(chunks) or None


def _describe_invalid_output(exc: Exception) -> str:
    cause = exc.__cause__
    if isinstance(cause, ValidationError):
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or '<root>'}: {e['msg']}" for e in cause.errors()[:5]
        )
        return f"The LLM response does not match the expected schema ({problems})."
    return f"The LLM response was not usable: {exc}."


def _to_llm_error(exc: Exception, timeout_seconds: float) -> LLMError:
    if isinstance(exc, RunTimedOut):
        return LLMTimeoutError(f"The LLM did not respond within {timeout_seconds:g} seconds.")
    if isinstance(exc, ModelTimeoutError | ModelRequestTimeout):
        return LLMTimeoutError("The LLM provider timed out.")
    if isinstance(exc, ModelAPIError):
        status = getattr(exc, "status_code", None)
        if status is not None:
            return LLMProviderError(f"The LLM provider returned HTTP {status}.")
        return LLMProviderError(f"The LLM provider request failed: {exc.__class__.__name__}.")
    if isinstance(exc, UnexpectedModelBehavior | UsageLimitExceeded):
        return LLMResponseFormatError(_describe_invalid_output(exc))
    # Unknown failure: never leak it as a 500.
    return LLMProviderError(f"Unexpected LLM provider failure: {exc.__class__.__name__}.")


class LogAnalyzer:
    def __init__(
        self, model: Model, timeout_seconds: float, max_patterns: int = 50, output_retries: int = 1
    ) -> None:
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_patterns = max_patterns
        self.output_retries = output_retries

    @property
    def provider_name(self) -> str:
        return provider_name(self.model)

    @property
    def model_name(self) -> str:
        return self.model.model_name

    def prepare(self, logs: list[LogLike]) -> PreparedInput:
        all_patterns = extract_patterns(logs, max_patterns=10_000)
        kept = all_patterns[: self.max_patterns]
        patterns = [p.to_prompt_dict() for p in kept]
        stats = compute_stats(logs)
        truncated = len(all_patterns) - len(kept)
        return PreparedInput(stats, patterns, truncated, build_user_prompt(stats, patterns, truncated))

    async def run(self, prepared: PreparedInput, allowed_log_ids: set[int]) -> AnalyzerOutcome:
        started = time.perf_counter()

        def elapsed() -> int:
            return int((time.perf_counter() - started) * 1000)

        with capture_run_messages() as messages:
            try:
                result = await analysis_agent.run(
                    prepared.user_prompt,
                    model=self.model,
                    deps=AnalysisDeps(frozenset(allowed_log_ids)),
                    retries={"output": self.output_retries},
                    timeout=self.timeout_seconds,  # cancels the in-flight request at the deadline
                )
            except Exception as exc:
                error = _to_llm_error(exc, self.timeout_seconds)
                raise LLMCallFailed(error, elapsed(), render_responses(messages)) from exc

        return AnalyzerOutcome(
            result=result.output,
            raw_response=render_responses(result.all_messages()) or "",
            duration_ms=elapsed(),
            usage=result.usage,
        )
