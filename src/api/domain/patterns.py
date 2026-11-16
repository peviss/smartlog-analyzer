"""Deterministic preprocessing before the LLM sees anything.

Raw logs are repetitive: thousands of lines differ only in ids, numbers or durations.
We collapse them into templates (e.g. "Payment failed for order <NUM>") with counts,
affected services and sample ids. The LLM then reasons over a few dozen patterns
instead of hundreds of lines, which keeps the prompt small, cheap and focused.

This is a lightweight take on log template mining (the Drain family of algorithms),
implemented with ordered regex masks so it has no extra dependencies.
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

# Order matters: more specific masks first.
_MASKS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"),
        "<UUID>",
    ),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "<EMAIL>"),
    (re.compile(r"\bhttps?://\S+"), "<URL>"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b"), "<IP>"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b"), "<TS>"),
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "<HEX>"),
    (re.compile(r"\b[0-9a-fA-F]{16,}\b"), "<HEX>"),
    (re.compile(r"\b\d+(?:\.\d+)?\s?(?:ms|s|sec|seconds|MB|KB|GB|%)\b"), "<NUM>"),
    (re.compile(r"(?<![A-Za-z])\d+(?:\.\d+)?(?![A-Za-z])"), "<NUM>"),
    (re.compile(r"'[^']*'"), "'<STR>'"),
    (re.compile(r'"[^"]*"'), '"<STR>"'),
]
_WHITESPACE = re.compile(r"\s+")
MAX_TEMPLATE_LENGTH = 300


class LogLike(Protocol):
    """What the preprocessing needs from a log (satisfied by models.LogEntry).

    `level` is the normalized level (critical/error/warn/info/debug), not the original
    ECS log.level, so WARN, warning and W are grouped together.
    """

    id: int
    timestamp: datetime
    service_name: str
    level: str
    message: str


def to_template(message: str) -> str:
    """Replace variable parts of a message with placeholders."""
    template = message.splitlines()[0] if message else ""
    for pattern, placeholder in _MASKS:
        template = pattern.sub(placeholder, template)
    template = _WHITESPACE.sub(" ", template).strip()
    return template[:MAX_TEMPLATE_LENGTH]


@dataclass
class Pattern:
    template: str
    level: str
    count: int = 0
    services: Counter = field(default_factory=Counter)
    sample_ids: list[int] = field(default_factory=list)
    sample_message: str = ""
    first_seen: datetime | None = None
    last_seen: datetime | None = None

    def to_prompt_dict(self) -> dict:
        return {
            "template": self.template,
            "level": self.level,
            "count": self.count,
            "services": dict(self.services.most_common()),
            "first_seen": self.first_seen.isoformat() if self.first_seen else None,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "sample_log_ids": self.sample_ids,
            "sample_message": self.sample_message,
        }


LEVEL_RANK = {"critical": 0, "error": 1, "warn": 2, "info": 3, "debug": 4}


def extract_patterns(logs: list[LogLike], max_patterns: int = 50, samples: int = 3) -> list[Pattern]:
    """Group logs by (level, template). Returns the most relevant patterns first:
    higher severity, then higher frequency."""
    groups: dict[tuple[str, str], Pattern] = {}
    for log in logs:
        key = (log.level, to_template(log.message))
        pattern = groups.get(key)
        if pattern is None:
            pattern = groups[key] = Pattern(
                template=key[1], level=log.level, sample_message=log.message[:500]
            )
        pattern.count += 1
        pattern.services[log.service_name] += 1
        if len(pattern.sample_ids) < samples:
            pattern.sample_ids.append(log.id)
        if pattern.first_seen is None or log.timestamp < pattern.first_seen:
            pattern.first_seen = log.timestamp
        if pattern.last_seen is None or log.timestamp > pattern.last_seen:
            pattern.last_seen = log.timestamp

    ordered = sorted(groups.values(), key=lambda p: (LEVEL_RANK.get(p.level, 9), -p.count, p.template))
    return ordered[:max_patterns]


def compute_stats(logs: list[LogLike]) -> dict:
    """Deterministic numbers the LLM should not have to count (LLMs are bad at counting)."""
    if not logs:
        return {"total": 0, "by_level": {}, "by_service": {}, "window": None}
    by_level = Counter(log.level for log in logs)
    by_service = Counter(log.service_name for log in logs)
    errors_by_service = Counter(log.service_name for log in logs if log.level in {"error", "critical"})
    timestamps = [log.timestamp for log in logs]
    return {
        "total": len(logs),
        "by_level": dict(by_level.most_common()),
        "by_service": dict(by_service.most_common()),
        "errors_by_service": dict(errors_by_service.most_common()),
        "window": {"from": min(timestamps).isoformat(), "to": max(timestamps).isoformat()},
    }
