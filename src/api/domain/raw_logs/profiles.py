"""Known JSON log schemas, translated to ECS with their own semantics.

A generic field mapping cannot read these correctly: OpenTelemetry wraps values in
AnyValue objects, CLEF has a message template, GELF prefixes custom fields with `_`,
and Docker, CloudWatch and Google Cloud wrap the application's own log line. Each
profile is recognized by fields only that schema has; anything else goes through the
generic normalization unchanged.
"""

import json
import re
from collections.abc import Callable
from typing import Any

from api.domain.raw_logs.normalize import ALIASES, _norm_key, apply_aliases, flatten

# ---------------------------------------------------------------- shared helpers


def merge_inner(base: dict[str, Any], text: str) -> dict[str, Any]:
    """`base` is wrapper metadata; `text` is the application's own line, parsed as plain text
    (JSON, logfmt, `ts LEVEL msg`...). What the line says wins over the wrapper."""
    from api.domain.raw_logs.text import line_records  # text parsing also uses these profiles

    inner = line_records(text.rstrip("\r\n"))
    merged = flatten(base)
    # Inner names are mapped to ECS first, so the line's `level` overrides the wrapper's log.level.
    for key, value in (apply_aliases(flatten(inner[0])) if inner else {"message": text}).items():
        if key == "labels" and isinstance(value, dict) and isinstance(merged.get("labels"), dict):
            merged["labels"] = {**merged["labels"], **value}
        elif key == "tags" and isinstance(value, list) and isinstance(merged.get("tags"), list):
            merged["tags"] = [*merged["tags"], *value]
        else:
            merged[key] = value
    return merged


def _last_segment(path: str) -> str:
    return path.rstrip("/").rsplit("/", 1)[-1]


# ---------------------------------------------------------------- OpenTelemetry (OTLP/JSON)

_OTEL_SEVERITY = [(4, "trace"), (8, "debug"), (12, "info"), (16, "warn"), (20, "error"), (24, "fatal")]
_OTEL_ATTRIBUTES = {
    "exception.type": "error.type",
    "exception.message": "error.message",
    "exception.stacktrace": "error.stack_trace",
    "deployment.environment": "service.environment",
    "deployment.environment.name": "service.environment",
    "code.function": "log.origin.function",
    "code.function.name": "log.origin.function",
    "code.filepath": "log.origin.file.name",
    "code.file.path": "log.origin.file.name",
    "code.lineno": "log.origin.file.line",
    "code.line.number": "log.origin.file.line",
    "thread.name": "process.thread.name",
    "thread.id": "process.thread.id",
}


def _any_value(value: Any) -> Any:
    """OTLP AnyValue -> plain value."""
    if not isinstance(value, dict):
        return value
    for key in ("stringValue", "boolValue", "doubleValue", "bytesValue"):
        if key in value:
            return value[key]
    if "intValue" in value:
        return int(value["intValue"])
    if "arrayValue" in value:
        return [_any_value(v) for v in value["arrayValue"].get("values", [])]
    if "kvlistValue" in value:
        return _attributes(value["kvlistValue"].get("values", []))
    return value


def _attributes(attributes: Any) -> dict[str, Any]:
    if isinstance(attributes, dict):  # some exporters write a plain map
        return attributes
    return {
        a["key"]: _any_value(a.get("value")) for a in attributes or [] if isinstance(a, dict) and "key" in a
    }


def _otel_level(record: dict[str, Any]) -> str | None:
    if record.get("severityText"):
        return str(record["severityText"])
    number = int(record.get("severityNumber") or 0)
    return next((name for top, name in _OTEL_SEVERITY if number and number <= top), None)


def _otel_record(record: dict[str, Any], resource: dict[str, Any], scope: str | None) -> dict[str, Any]:
    doc: dict[str, Any] = {}
    for key, value in resource.items():
        doc[_OTEL_ATTRIBUTES.get(key, key)] = value
    timestamp = next(
        (int(record[k]) for k in ("timeUnixNano", "observedTimeUnixNano") if int(record.get(k) or 0)), None
    )
    if timestamp:
        doc["@timestamp"] = timestamp
    if level := _otel_level(record):
        doc["log.level"] = level
    body = _any_value(record.get("body"))
    if body is not None:
        doc["message"] = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False, default=str)
    for key, target in (("traceId", "trace.id"), ("spanId", "span.id")):
        if record.get(key):
            doc[target] = record[key]
    if record.get("eventName"):
        doc["event.action"] = record["eventName"]
    for key, value in _attributes(record.get("attributes")).items():
        doc[_OTEL_ATTRIBUTES.get(key, key)] = value
    if scope and "log.logger" not in doc:
        doc["log.logger"] = scope
    return doc


def otel_export(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """`{"resourceLogs": [{"resource": ..., "scopeLogs": [{"logRecords": [...]}]}]}`"""
    docs = []
    for resource_logs in payload.get("resourceLogs") or []:
        resource = _attributes((resource_logs.get("resource") or {}).get("attributes"))
        scopes = resource_logs.get("scopeLogs") or resource_logs.get("instrumentationLibraryLogs") or []
        for scope_logs in scopes:
            scope = (scope_logs.get("scope") or scope_logs.get("instrumentationLibrary") or {}).get("name")
            docs += [_otel_record(r, resource, scope) for r in scope_logs.get("logRecords") or []]
    return docs


def _is_otel_record(obj: dict[str, Any]) -> bool:
    return bool({"timeUnixNano", "observedTimeUnixNano", "severityNumber", "severityText"} & obj.keys()) or (
        isinstance(obj.get("body"), dict) and bool({"stringValue", "kvlistValue"} & obj["body"].keys())
    )


def otel_record(obj: dict[str, Any]) -> list[dict[str, Any]]:
    resource = _attributes((obj.get("resource") or {}).get("attributes"))
    return [_otel_record(obj, resource, None)]


# ---------------------------------------------------------------- GELF (Graylog)


def gelf(obj: dict[str, Any]) -> list[dict[str, Any]]:
    doc: dict[str, Any] = {"message": obj.get("short_message")}
    if obj.get("full_message") and obj["full_message"] != obj.get("short_message"):
        doc["error.stack_trace"] = obj["full_message"]
    for key, target in (("host", "host.name"), ("timestamp", "@timestamp"), ("level", "log.level")):
        if key in obj:
            doc[target] = obj[key]
    if "facility" in obj:
        doc["log.syslog.facility.name"] = obj["facility"]
    if "file" in obj:
        doc["log.origin.file.name"] = obj["file"]
    if "line" in obj:
        doc["log.origin.file.line"] = obj["line"]
    for key, value in obj.items():
        if key.startswith("_") and key != "_id":
            name = key[1:]
            target = ALIASES.get(_norm_key(name))
            if target and target not in doc and not isinstance(value, dict | list):
                doc[target] = value
            else:
                doc[f"labels.{name}"] = value
    return [doc]


# ---------------------------------------------------------------- CLEF (Serilog compact JSON)

_TEMPLATE_HOLE = re.compile(r"\{[@$]?([A-Za-z_][\w]*)(?:[,:][^}]*)?\}")


def clef(obj: dict[str, Any]) -> list[dict[str, Any]]:
    properties = {k: v for k, v in obj.items() if not k.startswith("@")}
    doc: dict[str, Any] = dict(properties)
    if "@t" in obj:
        doc["@timestamp"] = obj["@t"]
    # CLEF: no @l means Information.
    doc["log.level"] = obj.get("@l", "Information")
    if "@m" in obj:
        doc["message"] = obj["@m"]
    elif "@mt" in obj:
        template = str(obj["@mt"]).replace("{{", "\0").replace("}}", "\1")

        def fill(match: re.Match[str]) -> str:
            value = properties.get(match.group(1))
            return (
                match.group(0) if value is None else (value if isinstance(value, str) else json.dumps(value))
            )

        doc["message"] = _TEMPLATE_HOLE.sub(fill, template).replace("\0", "{").replace("\1", "}")
    if "@x" in obj:
        doc["error.stack_trace"] = obj["@x"]
        first = str(obj["@x"]).splitlines()[0] if obj["@x"] else ""
        if ":" in first and " " not in first.split(":", 1)[0]:
            doc["error.type"] = first.split(":", 1)[0]
    for key, target in (("@i", "event.code"), ("@tr", "trace.id"), ("@sp", "span.id")):
        if key in obj:
            doc[target] = obj[key]
    return [doc]


# ---------------------------------------------------------------- Docker json-file


def docker(obj: dict[str, Any]) -> list[dict[str, Any]]:
    base: dict[str, Any] = {"@timestamp": obj.get("time")}
    if obj.get("stream"):
        base["labels.stream"] = obj["stream"]
    for key, value in (obj.get("attrs") or {}).items():
        base[f"labels.{key}"] = value
    return [merge_inner(base, obj["log"])]


# ---------------------------------------------------------------- Google Cloud Logging


def _gcp(obj: dict[str, Any]) -> dict[str, Any]:
    base: dict[str, Any] = {
        "@timestamp": obj.get("timestamp") or obj.get("receiveTimestamp"),
        "log.level": obj.get("severity"),
        "cloud.provider": "gcp",
    }
    if obj.get("insertId"):
        base["event.id"] = obj["insertId"]
    if obj.get("trace"):
        base["trace.id"] = _last_segment(str(obj["trace"]))
    if obj.get("spanId"):
        base["span.id"] = obj["spanId"]
    if obj.get("logName"):
        base["event.dataset"] = _last_segment(str(obj["logName"]))
    resource = obj.get("resource") or {}
    rlabels = resource.get("labels") or {}
    service = next(
        (
            rlabels[k]
            for k in ("service_name", "container_name", "module_id", "function_name", "job")
            if rlabels.get(k)
        ),
        None,
    )
    if service:
        base["service.name"] = service
    if rlabels.get("project_id"):
        base["cloud.project.id"] = rlabels["project_id"]
    region = rlabels.get("location") or rlabels.get("region") or rlabels.get("zone")
    if region:
        base["cloud.region"] = region
    if resource.get("type"):
        base["labels.resource_type"] = resource["type"]
    for key, value in (obj.get("labels") or {}).items():
        base[f"labels.{key}"] = value
    http = obj.get("httpRequest") or {}
    for key, target in (
        ("requestMethod", "http.request.method"),
        ("requestUrl", "url.original"),
        ("status", "http.response.status_code"),
        ("userAgent", "user_agent.original"),
        ("remoteIp", "source.ip"),
        ("latency", "labels.latency"),
    ):
        if http.get(key) is not None:
            base[target] = http[key]
    if isinstance(obj.get("jsonPayload"), dict):
        return {**base, **flatten(obj["jsonPayload"])}
    if obj.get("textPayload") is not None:
        return merge_inner(base, str(obj["textPayload"]))
    if obj.get("protoPayload") is not None:
        base["message"] = json.dumps(obj["protoPayload"], ensure_ascii=False, default=str)
    return base


def gcp(obj: dict[str, Any]) -> list[dict[str, Any]]:
    return [_gcp(obj)]


# ---------------------------------------------------------------- AWS CloudWatch Logs


def _cloudwatch_event(
    event: dict[str, Any], group: str | None, stream: str | None, owner: str | None
) -> dict[str, Any]:
    base: dict[str, Any] = {"@timestamp": event.get("timestamp"), "cloud.provider": "aws"}
    if event.get("id"):
        base["event.id"] = event["id"]
    if group:
        base["labels.log_group"] = group
        base["service.name"] = _last_segment(group)
    if stream:
        base["labels.log_stream"] = stream
    if owner:
        base["cloud.account.id"] = owner
    return merge_inner(base, str(event.get("message", "")))


def cloudwatch_subscription(obj: dict[str, Any]) -> list[dict[str, Any]]:
    """Subscription filter payload: `{"logGroup", "logStream", "logEvents": [...]}`."""
    if obj.get("messageType") == "CONTROL_MESSAGE":
        return []
    group, stream, owner = obj.get("logGroup"), obj.get("logStream"), obj.get("owner")
    return [_cloudwatch_event(e, group, stream, owner) for e in obj["logEvents"] if isinstance(e, dict)]


def cloudwatch_event(obj: dict[str, Any]) -> list[dict[str, Any]]:
    """One exported event: `{"timestamp": 1731751200000, "message": "...", "logStreamName"?}`."""
    group = obj.get("logGroupName") or obj.get("logGroup")
    stream = obj.get("logStreamName") or obj.get("logStream")
    return [_cloudwatch_event(obj, group, stream, None)]


# ---------------------------------------------------------------- detection

Profile = Callable[[dict[str, Any]], list[dict[str, Any]]]

PROFILES: list[tuple[str, Callable[[dict[str, Any]], bool], Profile]] = [
    ("opentelemetry", lambda o: isinstance(o.get("resourceLogs"), list), otel_export),
    ("cloudwatch", lambda o: isinstance(o.get("logEvents"), list), cloudwatch_subscription),
    ("opentelemetry", _is_otel_record, otel_record),
    ("gelf", lambda o: "short_message" in o, gelf),
    ("clef", lambda o: "@t" in o or "@mt" in o, clef),
    (
        "gcp",
        lambda o: (
            "severity" in o
            and bool({"jsonPayload", "textPayload", "protoPayload", "logName", "insertId"} & o.keys())
        ),
        gcp,
    ),
    (
        "docker",
        lambda o: (
            isinstance(o.get("log"), str) and "message" not in o and bool({"stream", "time"} & o.keys())
        ),
        docker,
    ),
    (
        "cloudwatch",
        lambda o: (
            isinstance(o.get("message"), str)
            and isinstance(o.get("timestamp"), int)
            and bool(
                {"ingestionTime", "logStreamName", "eventId"} & o.keys()
                or set(o) <= {"id", "timestamp", "message"}
            )
        ),
        cloudwatch_event,
    ),
]


def detect_profile(obj: dict[str, Any]) -> str | None:
    return next((name for name, matches, _ in PROFILES if matches(obj)), None)


def convert(obj: dict[str, Any]) -> list[dict[str, Any]]:
    """A JSON object from any source -> one or more records (not yet normalized)."""
    for _, matches, profile in PROFILES:
        if matches(obj):
            return profile(obj)
    return [obj]
