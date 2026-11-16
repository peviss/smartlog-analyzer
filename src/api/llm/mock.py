"""A realistic mock LLM, as a Pydantic AI `FunctionModel`.

It reads the same prompt a real model would get, applies keyword rules to the log
patterns and answers through the structured-output tool, so its output goes through the
exact same agent, validation and timeout path as a real model. It lets the whole app run
and be demoed with no API key and no cost.
"""

import asyncio
import re
from collections import defaultdict
from dataclasses import dataclass

from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from api.llm.prompt import extract_payload


@dataclass(frozen=True)
class Rule:
    category: str
    pattern: re.Pattern[str]
    base_severity: str
    description: str
    suggestion_title: str
    suggestion: str


RULES: list[Rule] = [
    Rule(
        "Database connectivity",
        re.compile(r"database|db |postgres|mysql|connection pool|deadlock|sql", re.I),
        "high",
        "Services are failing to obtain or keep database connections, or queries are failing.",
        "Review database connection pool and health",
        "Check pool size versus concurrent load, slow queries holding connections and the database's own health metrics.",
    ),
    Rule(
        "Timeouts and latency",
        re.compile(r"timeout|timed out|slow|latency|exceeded|took <NUM>", re.I),
        "medium",
        "Operations are exceeding their expected duration or timing out.",
        "Investigate slow dependencies and tune timeouts",
        "Trace the slowest requests end to end, add or tune timeouts and circuit breakers on the affected calls.",
    ),
    Rule(
        "Authentication and authorization",
        re.compile(r"auth|unauthori[sz]ed|forbidden|token|login|credential|permission|jwt", re.I),
        "medium",
        "Requests are being rejected because of invalid credentials, expired tokens or missing permissions.",
        "Check token issuing and expiry configuration",
        "Verify clock sync, token TTLs and recent changes to auth configuration; watch for brute-force patterns.",
    ),
    Rule(
        "External dependency failures",
        re.compile(
            r"upstream|gateway|<NUM> (?:bad gateway|service unavailable)|5\d\d|third.party|stripe|provider|refused|unreachable|dns",
            re.I,
        ),
        "high",
        "Calls to external or downstream services are failing.",
        "Add retries with backoff and a fallback for the failing dependency",
        "Confirm the dependency's status, add bounded retries with jitter and degrade gracefully when it is unavailable.",
    ),
    Rule(
        "Resource exhaustion",
        re.compile(r"out of memory|oom|memory|disk|cpu|heap|quota|rate limit|too many", re.I),
        "high",
        "Services are running out of memory, disk, CPU or hitting rate limits.",
        "Review resource limits and usage trends",
        "Compare limits with actual usage, look for leaks after recent deploys and add alerts before exhaustion.",
    ),
    Rule(
        "Application errors",
        re.compile(r"exception|error|null|undefined|traceback|panic|failed|failure|invalid", re.I),
        "medium",
        "Unhandled exceptions or failed operations inside the application code.",
        "Fix the most frequent application exceptions",
        "Start with the highest-count error template, reproduce it from the sample logs and add a regression test.",
    ),
]

SEVERITY_ORDER = ["low", "medium", "high", "critical"]


def _bump(severity: str, steps: int) -> str:
    idx = min(SEVERITY_ORDER.index(severity) + steps, len(SEVERITY_ORDER) - 1)
    return SEVERITY_ORDER[idx]


def build_mock_analysis(payload: dict) -> dict:
    stats = payload.get("stats", {})
    patterns = [p for p in payload.get("patterns", []) if p.get("level") in {"warn", "error", "critical"}]

    buckets: dict[str, dict] = defaultdict(
        lambda: {"occurrences": 0, "services": set(), "ids": [], "levels": set(), "templates": []}
    )
    rules_by_name = {r.category: r for r in RULES}
    for p in patterns:
        rule = next((r for r in RULES if r.pattern.search(p["template"])), None)
        name = rule.category if rule else "Other warnings"
        b = buckets[name]
        b["occurrences"] += p["count"]
        b["services"].update(p.get("services", {}).keys())
        b["ids"].extend(p.get("sample_log_ids", []))
        b["levels"].add(p["level"])
        b["templates"].append((p["count"], p["template"]))

    categories, suggestions = [], []
    for name, b in sorted(buckets.items(), key=lambda kv: -kv[1]["occurrences"]):
        rule = rules_by_name.get(name)
        severity = rule.base_severity if rule else "low"
        if "critical" in b["levels"]:
            severity = "critical"
        elif b["occurrences"] >= 20:
            severity = _bump(severity, 1)
        top = max(b["templates"])[1]
        categories.append(
            {
                "name": name,
                "severity": severity,
                "description": (rule.description if rule else "Warnings that do not match a known category.")
                + f' Most frequent pattern: "{top}".',
                "occurrences": b["occurrences"],
                "services": sorted(b["services"]),
                "example_log_ids": b["ids"][:5],
            }
        )
        if rule:
            suggestions.append(
                {
                    "title": rule.suggestion_title,
                    "description": rule.suggestion
                    + f" Affected services: {', '.join(sorted(b['services']))}.",
                    "priority": "high" if severity in {"high", "critical"} else "medium",
                    "category": name,
                }
            )

    total = stats.get("total", 0)
    by_level = stats.get("by_level", {})
    problems = by_level.get("error", 0) + by_level.get("critical", 0)
    if categories:
        worst_service = next(iter(stats.get("errors_by_service", {})), None)
        summary = (
            f"Analysed {total} log entries: {problems} errors/critical and {by_level.get('warn', 0)} warnings. "
            f"The main issue is {categories[0]['name'].lower()} ({categories[0]['occurrences']} occurrences)"
            + (f", and {worst_service} is the service with the most errors." if worst_service else ".")
            + f" {len(categories)} problem categories were detected."
        )
    else:
        summary = f"Analysed {total} log entries and found no warnings or errors. The services look healthy."

    return {"summary": summary, "categories": categories, "suggestions": suggestions}


def last_user_prompt(messages: list[ModelMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                    return part.content
    raise ValueError("no user prompt in the request")


class MockModel(FunctionModel):
    def __init__(self, latency_ms: int = 300, model_name: str = "mock-rules-v1") -> None:
        self.latency_ms = latency_ms
        super().__init__(self._respond, model_name=model_name)
        self._system = "mock"

    async def _respond(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if self.latency_ms:
            await asyncio.sleep(self.latency_ms / 1000)
        analysis = build_mock_analysis(extract_payload(last_user_prompt(messages)))
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, analysis)])
