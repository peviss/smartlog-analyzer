"""Controllable Pydantic AI models for tests."""

import asyncio
import json
from collections.abc import Callable
from typing import Any

from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from api.llm.mock import last_user_prompt

VALID_RESPONSE = {
    "summary": "payments-api cannot get database connections; checkout is failing.",
    "categories": [
        {
            "name": "Database connectivity",
            "severity": "critical",
            "description": "Connection pool exhausted in payments-api.",
            "occurrences": 3,
            "services": ["payments-api"],
            "example_log_ids": [],
        }
    ],
    "suggestions": [
        {
            "title": "Increase pool size",
            "description": "Raise the pool size and find slow queries.",
            "priority": "high",
            "category": "Database connectivity",
        }
    ],
}


class Text(str):
    """A plain-text reply (no structured-output tool call)."""


Reply = dict | str | Text | Exception


class FakeModel(FunctionModel):
    """Replies with a fixed answer (or a sequence of answers, one per request) and records prompts.

    - `dict` or `str` (JSON): sent as the structured-output tool call
    - `Text`: sent as plain text
    - `Exception`: raised from the model
    """

    def __init__(
        self,
        *replies: Reply | Callable[[str], Reply],
        delay: float = 0.0,
        model_name: str = "fake-model",
    ) -> None:
        self.replies = list(replies) or [VALID_RESPONSE]
        self.delay = delay
        self.calls: list[tuple[str | None, str]] = []
        super().__init__(self._respond, model_name=model_name)
        self._system = "fake"

    @property
    def text(self) -> str:
        """The JSON the first reply sends, as stored in raw_response."""
        reply = self.replies[0]
        return reply if isinstance(reply, str) else json.dumps(reply)

    async def _respond(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        user = last_user_prompt(messages)
        self.calls.append((info.instructions, user))
        if self.delay:
            await asyncio.sleep(self.delay)
        reply: Any = self.replies[min(len(self.calls), len(self.replies)) - 1]
        if callable(reply):
            reply = reply(user)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, Text):
            return ModelResponse(parts=[TextPart(str(reply))])
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, reply)])


def valid_model(**overrides) -> FakeModel:
    return FakeModel(json.dumps({**VALID_RESPONSE, **overrides}))
