"""Plain-text log lines -> records.

Recognized in this order (first match wins):
  1. JSON object (NDJSON), through the known-schema profiles;
  2. Kubernetes CRI: `2026-11-16T10:00:00.1Z stderr F <application line>`;
  3. syslog RFC 5424: `<34>1 2026-11-16T10:00:00Z host app 123 ID47 - message`;
  4. syslog RFC 3164: `<34>Nov 16 10:00:00 host app[123]: message`;
  5. Apache / nginx access log (common and combined);
  6. klog (Kubernetes, Go): `E1116 10:00:00.123456 1 server.go:42] message`;
  7. logfmt: `time=... level=error service=payments msg="Payment failed"`;
  8. generic: optional timestamp, then a level within the first tokens, then the message;
     Python logging (`ts - logger - LEVEL - msg`), logback and log4j
     (`ts LEVEL [thread] logger - msg`) also yield the logger and the thread.
Wrapped lines (CRI, syslog) are parsed again, so a JSON or logfmt line inside syslog keeps
its fields.
"""

import json
import re
from datetime import UTC, datetime
from typing import Any

from api.domain import ecs
from api.domain.raw_logs import profiles
from api.domain.raw_logs.normalize import normalize_document

_ISO = r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d{1,9})?(?:Z|[+-]\d{2}:?\d{2})?"
_TIMESTAMP_PREFIXES = [
    _ISO,
    r"\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}(?:[.,]\d{1,9})?",
    r"\d{2}/[A-Z][a-z]{2}/\d{4}:\d{2}:\d{2}:\d{2} [+-]\d{4}",
    r"\d{2}-[A-Z][a-z]{2}-\d{4} \d{2}:\d{2}:\d{2}(?:\.\d{1,6})?",
    r"[A-Z][a-z]{2} \d{1,2},? \d{4},? \d{1,2}:\d{2}:\d{2}(?: ?[AP]M)?",
    r"\d{13}(?=\s)|\d{10}(?:\.\d{1,9})?(?=\s)",  # epoch ms or s
]
_GENERIC = re.compile(rf"^\s*\[?(?P<ts>{'|'.join(_TIMESTAMP_PREFIXES)})\]?\s*(?P<rest>.*)$")
_CRI = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})) (?P<stream>stdout|stderr) [FP] (?P<msg>.*)$"
)
_SYSLOG_5424 = re.compile(
    r"^<(?P<pri>\d{1,3})>1 (?P<ts>\S+) (?P<host>\S+) (?P<app>\S+) (?P<pid>\S+) (?P<msgid>\S+) "
    r"(?P<sd>-|(?:\[(?:[^\]\\]|\\.)*\])+)(?: (?P<msg>.*))?$"
)
_SYSLOG_3164 = re.compile(
    r"^(?:<(?P<pri>\d{1,3})>)?(?P<ts>[A-Z][a-z]{2} [ \d]\d \d{2}:\d{2}:\d{2}) (?P<host>\S+) "
    r"(?P<app>[^\s:\[]+)(?:\[(?P<pid>\d+)\])?: ?(?P<msg>.*)$"
)
_ACCESS = re.compile(
    r'^(?P<ip>\S+) \S+ (?P<user>\S+) \[(?P<ts>[^\]]+)\] "(?P<request>[^"]*)" (?P<status>\d{3}) (?P<bytes>\d+|-)'
    r'(?: "(?P<referrer>[^"]*)" "(?P<agent>[^"]*)")?'
)
_KLOG = re.compile(
    r"^(?P<lvl>[IWEF])(?P<md>\d{4}) (?P<time>\d{2}:\d{2}:\d{2}\.\d+)\s+(?P<pid>\d+) (?P<file>[^:\s]+):(?P<line>\d+)\] ?(?P<msg>.*)$"
)
_LOGFMT_PAIR = re.compile(r'([A-Za-z_@][\w.@-]*)=("(?:[^"\\]|\\.)*"|\S*)')
_LEVEL_TOKEN = re.compile(r"^[\[(<]?(?P<level>[A-Za-z]+)[\])>]?:?$")
_THREAD_PREFIX = re.compile(r"^\[(?P<thread>[^\]]+)\]\s+(?P<rest>.*)$")
_LOGGER_PREFIX = re.compile(r"^(?P<logger>[A-Za-z_$][\w$]*(?:\.[\w$]+)+)\s+[-:]\s+(?P<msg>.+)$", re.S)
_WORD_LOGGER_PREFIX = re.compile(r"^(?P<logger>[A-Za-z_$][\w$]*)\s+[-:]\s+(?P<msg>.+)$", re.S)

_SYSLOG_NAMES = ["emerg", "alert", "crit", "err", "warning", "notice", "info", "debug"]
_KLOG_LEVELS = {"I": "info", "W": "warn", "E": "error", "F": "fatal"}
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r"}


# ---------------------------------------------------------------- helpers


def _level_name(token: str, *, lenient: bool) -> str | None:
    """A known level spelling. Lowercase words count only when `lenient`, single letters only
    when bracketed: otherwise "Info about..." or "connection error" would be read as levels."""
    match = _LEVEL_TOKEN.match(token)
    if not match:
        return None
    word = match.group("level")
    bracketed = token[:1] in "[(<"
    if len(word) < 3 and not bracketed:
        return None
    if not (bracketed or word.isupper() or lenient):
        return None
    try:
        ecs.normalize_level(word)
    except ValueError:
        return None
    return word


def _split_level(text: str, *, after_timestamp: bool, max_tokens: int = 5) -> tuple[str | None, str, str]:
    """Find a level among the first tokens: (level, text before it, message after it)."""
    tokens = list(re.finditer(r"\S+", text))[:max_tokens]
    for index, token in enumerate(tokens):
        level = _level_name(token.group(), lenient=after_timestamp and index == 0)
        if level:
            return level, text[: token.start()], text[token.end() :].lstrip(" \t-:|>")
    return None, "", text


def _iso(ts: str) -> str:
    return ts.replace(",", ".").replace(" ", "T", 1) if re.fullmatch(_ISO, ts) else ts


def _syslog_level(pri: str | None) -> str | None:
    return _SYSLOG_NAMES[int(pri) % 8] if pri is not None and int(pri) < 192 else None


def _without_year(month_day_time: str, now: datetime, fmt: str) -> str:
    """Formats with no year (RFC 3164, klog): this year, or last year if that is in the future."""
    parsed = datetime.strptime(f"{now.year} {month_day_time}", f"%Y {fmt}").replace(tzinfo=UTC)
    if parsed > now:
        parsed = parsed.replace(year=now.year - 1)
    return parsed.isoformat()


def _unquote(value: str) -> str:
    """logfmt value: strip quotes and undo backslash escapes (keeps non-ASCII text intact)."""
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return re.sub(r"\\(.)", lambda m: _ESCAPES.get(m.group(1), m.group(1)), value[1:-1])
    return value


# ---------------------------------------------------------------- formats


def _syslog_5424(m: re.Match[str]) -> dict[str, Any]:
    base: dict[str, Any] = {}
    for group, target in (
        ("ts", "@timestamp"),
        ("host", "host.name"),
        ("app", "service.name"),
    ):
        if m.group(group) != "-":
            base[target] = m.group(group)
    if m.group("pid").isdigit():  # PROCID may be any text in RFC 5424
        base["process.pid"] = int(m.group("pid"))
    if level := _syslog_level(m.group("pri")):
        base["log.level"] = level
    message = (m.group("msg") or "").lstrip("\ufeff")
    return profiles.merge_inner(base, message) if message else {**base, "message": ""}


def _syslog_3164(m: re.Match[str], now: datetime) -> dict[str, Any]:
    base: dict[str, Any] = {
        "@timestamp": _without_year(m.group("ts"), now, "%b %d %H:%M:%S"),
        "host.name": m.group("host"),
        "service.name": m.group("app"),
    }
    if m.group("pid"):
        base["process.pid"] = int(m.group("pid"))
    if level := _syslog_level(m.group("pri")):
        base["log.level"] = level
    return profiles.merge_inner(base, m.group("msg"))


def _access_log(m: re.Match[str]) -> dict[str, Any]:
    status = int(m.group("status"))
    request = m.group("request")
    doc: dict[str, Any] = {
        "@timestamp": m.group("ts"),
        "source.ip": m.group("ip"),
        "http.response.status_code": status,
        "message": f"{request} {status}".strip(),
        "log.level": "error" if status >= 500 else "warn" if status >= 400 else "info",
        "event.category": "web",
    }
    parts = request.split()
    if len(parts) == 3:
        doc["http.request.method"], doc["url.original"] = parts[0], parts[1]
        doc["http.version"] = parts[2].removeprefix("HTTP/")
    if m.group("user") != "-":
        doc["user.name"] = m.group("user")
    if m.group("bytes") != "-":
        doc["http.response.body.bytes"] = int(m.group("bytes"))
    if m.group("referrer") not in (None, "-"):
        doc["http.request.referrer"] = m.group("referrer")
    if m.group("agent") not in (None, "-"):
        doc["user_agent.original"] = m.group("agent")
    return doc


def _klog(m: re.Match[str], now: datetime) -> dict[str, Any]:
    md = m.group("md")
    return {
        "@timestamp": _without_year(f"{md[:2]}-{md[2:]} {m.group('time')}", now, "%m-%d %H:%M:%S.%f"),
        "log.level": _KLOG_LEVELS[m.group("lvl")],
        "process.pid": int(m.group("pid")),
        "log.origin.file.name": m.group("file"),
        "log.origin.file.line": int(m.group("line")),
        "message": m.group("msg"),
    }


def _generic(line: str) -> dict[str, Any]:
    doc: dict[str, Any] = {}
    rest = line
    if m := _GENERIC.match(line):
        doc["@timestamp"] = _iso(m.group("ts"))
        rest = m.group("rest")
    level, before, message = _split_level(rest, after_timestamp="@timestamp" in doc)
    if not level:
        doc["message"] = rest or line
        return doc
    doc["log.level"] = level
    # Before the level: "- payments -" (Python logging) or "[main]" (log4j).
    context = before.strip(" \t-|:")
    if thread := re.fullmatch(r"\[([^\]]+)\]", context):
        doc["process.thread.name"] = thread.group(1)
    elif context and re.fullmatch(r"[\w.$:/-]+", context):
        doc["log.logger"] = context
    # After the level: "[thread] logger - message" (logback) or "logger - message".
    if m := _THREAD_PREFIX.match(message):
        doc.setdefault("process.thread.name", m.group("thread"))
        message = m.group("rest")
    logger = _LOGGER_PREFIX.match(message) or (
        _WORD_LOGGER_PREFIX.match(message) if "process.thread.name" in doc else None
    )
    if logger and "log.logger" not in doc:
        doc["log.logger"] = logger.group("logger")
        message = logger.group("msg")
    doc["message"] = message or rest or line
    return doc


def line_records(line: str, now: datetime | None = None) -> list[dict[str, Any]]:
    """One plain-text line -> records (usually one; an OTLP or CloudWatch JSON line can hold many).
    Not normalized yet."""
    now = now or datetime.now(UTC)
    stripped = line.strip()

    if stripped.startswith("{"):
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError:
            value = None
        if isinstance(value, dict):
            return profiles.convert(value)

    if m := _CRI.match(stripped):
        base = {"@timestamp": m.group("ts"), "labels.stream": m.group("stream")}
        return [profiles.merge_inner(base, m.group("msg"))]
    if m := _SYSLOG_5424.match(stripped):
        return [_syslog_5424(m)]
    if m := _SYSLOG_3164.match(stripped):
        return [_syslog_3164(m, now)]
    if m := _ACCESS.match(stripped):
        return [_access_log(m)]
    if m := _KLOG.match(stripped):
        return [_klog(m, now)]

    pairs = _LOGFMT_PAIR.findall(stripped)
    if len(pairs) >= 2 and any(k.lower() in {"msg", "message", "level", "lvl"} for k, _ in pairs):
        return [{k: _unquote(v) for k, v in pairs}]

    return [_generic(stripped)]


def parse_line(line: str, now: datetime | None = None) -> dict[str, Any]:
    """The first record of a line, normalized to ECS (convenient for single-record lines)."""
    records = line_records(line, now)
    return normalize_document(records[0]) if records else {}
