"""Make any log record fit ECS: field names, value formats and size limits.

Used only by the import path. It never rejects a record: what does not fit is moved, and
every assumption is visible as a processing tag (`_message_missing`, `_truncated`...) or a
label (`original_timestamp`, `original_service_name`...). `LogCreate` validates the result.
"""

import json
import re
from typing import Any

from api.domain.raw_logs.timestamps import parse_timestamp

# Same limits as api.schemas.logs: values over them are trimmed here instead of rejected there.
MAX_MESSAGE_LENGTH = 10_000
MAX_TEXT_LENGTH = 32_768
MAX_EXTRA_BYTES = 8_000  # schema limit is 8192; keep a margin for JSON nesting
MAX_TAGS = 20
MAX_LABELS = 50
_SHORT = {
    "log.logger": 256,
    "service.version": 64,
    "service.environment": 64,
    "host.name": 255,
    "error.type": 256,
}

TAG_LEVEL_MISSING = "_level_missing"
TAG_MESSAGE_MISSING = "_message_missing"
TAG_TIMESTAMP_UNPARSED = "_timestamp_unparsed"
TAG_TRUNCATED = "_truncated"
TAG_FIELDS_DROPPED = "_fields_dropped"
_PROCESSING_TAG_ROOM = 4  # user tags are capped so these always fit

# ECS leaf fields stored in their own columns: a dict or list there is serialized, not flattened.
CORE_FIELDS = frozenset(
    {
        "@timestamp", "message", "log.level", "log.logger", "service.name", "service.version",
        "service.environment", "host.name", "trace.id", "span.id", "transaction.id", "error.type",
        "error.message", "error.stack_trace", "event.id", "event.dataset", "event.original", "ecs.version",
    }
)  # fmt: skip
_OPAQUE = frozenset({"labels", "tags"})

# ECS objects that sometimes arrive as a plain value: where that value goes.
_OBJECT_SLOTS = {
    "error": "error.message",
    "event": "event.action",
    "trace": "trace.id",
    "span": "span.id",
    "transaction": "transaction.id",
    "ecs": "ecs.version",
    "service": "service.name",
    "host": "host.name",
    "log": "message",
    "user": "user.name",
    "process": "process.name",
    "url": "url.original",
    "source": "source.ip",
    "client": "client.ip",
    "user_agent": "user_agent.original",
    "container": "container.id",
    "file": "file.path",
    "cloud": "cloud.provider",
}


def _norm_key(key: str) -> str:
    """`traceId`, `trace_id`, `Trace-Id` and `trace id` are the same name."""
    return re.sub(r"[\s_\-]", "", key.lower())


_ALIAS_GROUPS: dict[str, list[str]] = {
    "@timestamp": ["timestamp", "time", "ts", "date", "datetime", "@t", "asctime", "eventtime", "logtime"],
    "log.level": ["level", "severity", "lvl", "loglevel", "levelname", "@l", "sev"],
    "message": ["message", "msg", "@m", "text", "logmessage"],
    "service.name": ["service", "app", "application", "servicename", "appname", "applicationname"],
    "host.name": ["host", "hostname", "machinename", "computer", "computername"],
    "log.logger": ["logger", "loggername", "category", "sourcecontext"],
    "service.environment": ["env", "environment", "environmentname", "deploymentenvironment"],
    "service.version": ["version", "serviceversion", "appversion"],
    "trace.id": ["traceid", "trace"],
    "span.id": ["spanid"],
    "event.id": ["eventid"],
    "event.dataset": ["dataset"],
    "error.stack_trace": ["stacktrace", "stack", "excinfo", "exception"],
    "error.type": ["errortype", "exceptiontype", "errorclass"],
    "error.message": ["errormessage", "exceptionmessage", "err"],
    "process.thread.name": ["thread", "threadname"],
    "process.thread.id": ["threadid", "tid"],
    "process.pid": ["pid", "processid"],
    "user.name": ["user", "username"],
    "source.ip": ["clientip", "remoteaddr", "remoteip", "srcip", "sourceip"],
    "http.request.method": ["method", "httpmethod", "requestmethod"],
    "url.original": ["url", "uri", "requesturl", "requesturi"],
    "http.response.status_code": ["status", "statuscode", "httpstatus", "responsestatus"],
    "user_agent.original": ["useragent", "httpuseragent"],
    "tags": ["tags"],
    "labels": ["labels"],
}
ALIASES: dict[str, str] = {alias: target for target, names in _ALIAS_GROUPS.items() for alias in names}


def _present(value: Any) -> bool:
    return value is not None and value != ""


def has_field(doc: dict[str, Any], path: str) -> bool:
    """True if `path` has a value, as a dotted key or as nested objects."""
    if _present(doc.get(path)):
        return True
    node: Any = doc
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return _present(node)


def _alias_fits(target: str, value: Any) -> bool:
    if target == "tags":
        return isinstance(value, list | str)
    if target == "labels":
        return isinstance(value, dict)
    if target == "message":
        return value is not None
    if target == "http.response.status_code":
        return isinstance(value, int) or (isinstance(value, str) and value.isdigit())
    return not isinstance(value, dict | list)


def apply_aliases(doc: dict[str, Any]) -> dict[str, Any]:
    """Rename common non-ECS keys to ECS. Never overwrites a field that is already set."""
    out: dict[str, Any] = {}
    renamed: dict[str, Any] = {}
    for key, value in doc.items():
        target = ALIASES.get(_norm_key(key)) if isinstance(key, str) and "." not in key else None
        if target and target != key and _alias_fits(target, value):
            renamed.setdefault(target, value)
        else:
            out[key] = value
    for target, value in renamed.items():
        if not has_field(out, target):
            out[target] = value
    return out


def flatten(doc: dict[str, Any], prefix: str = "", out: dict[str, Any] | None = None) -> dict[str, Any]:
    """Nested objects -> dotted keys (labels, tags and ECS leaf fields are kept whole).
    On a clash the first value wins, so `LogCreate` never sees the same field twice."""
    out = {} if out is None else out
    for key, value in doc.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            if not value:
                continue
            if path not in CORE_FIELDS and path not in _OPAQUE:
                flatten(value, f"{path}.", out)
                continue
        out.setdefault(path, value)
    return out


def add_tag(doc: dict[str, Any], tag: str) -> None:
    tags = doc.get("tags")
    if not isinstance(tags, list):
        tags = [] if tags is None else [str(tags)]
    if tag not in tags:
        tags.append(tag)
    doc["tags"] = tags


def add_label(doc: dict[str, Any], key: str, value: Any) -> None:
    labels = doc.get("labels")
    if not isinstance(labels, dict):
        labels = {}
    if key in labels or len(labels) < MAX_LABELS:
        labels[key[:100]] = str(value)[:1024]
    doc["labels"] = labels


def _to_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict | list):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


# ---------------------------------------------------------------- steps


def _fit_object_slots(doc: dict[str, Any]) -> None:
    """A plain value where ECS has an object (`"error": "boom"`) goes to that object's main field."""
    for slot, target in _OBJECT_SLOTS.items():
        if slot not in doc or isinstance(doc[slot], dict):
            continue
        value = doc.pop(slot)
        if not _present(value):
            continue
        if not _present(doc.get(target)):
            doc[target] = value
        elif slot == "log" and not _present(doc.get("event.original")):
            doc["event.original"] = _to_text(value)
        else:
            add_label(doc, slot, _to_text(value))
    # Any other key that is both a value and a prefix (`a` and `a.b`): keep both.
    prefixes = {k.rsplit(".", 1)[0] for k in doc if "." in k}
    for key in [k for k in doc if k in prefixes and k not in _OPAQUE]:
        doc[f"{key}.value"] = doc.pop(key)


def _normalize_tags(doc: dict[str, Any]) -> None:
    value = doc.get("tags")
    if value is None:
        return
    if isinstance(value, str):
        items: list[Any] = re.split(r"[;,|]", value)
    elif isinstance(value, list):
        items = value
    elif isinstance(value, dict):
        items = [f"{k}:{v}" for k, v in value.items()]
    else:
        items = [value]
    tags: list[str] = []
    for item in items:
        tag = _to_text(item).strip()[:100] if item is not None else ""
        if tag and tag not in tags:
            tags.append(tag)
    if tags:
        doc["tags"] = tags[: MAX_TAGS - _PROCESSING_TAG_ROOM]
    else:
        doc.pop("tags")


def _flatten_labels(value: dict[str, Any], prefix: str, out: dict[str, str]) -> None:
    for key, item in value.items():
        path = f"{prefix}{key}"
        if isinstance(item, dict):
            _flatten_labels(item, f"{path}.", out)
        elif item is None:
            continue
        elif isinstance(item, bool):
            out.setdefault(path, str(item).lower())
        elif isinstance(item, list):
            out.setdefault(path, ",".join(_to_text(i) for i in item))
        else:
            out.setdefault(path, _to_text(item))


def _normalize_labels(doc: dict[str, Any]) -> None:
    """ECS labels are a flat map of strings: nested objects and lists are flattened."""
    value = doc.pop("labels", None)
    source: dict[str, Any] = (
        value if isinstance(value, dict) else ({"labels": value} if _present(value) else {})
    )
    for key in [k for k in doc if k.startswith("labels.")]:
        source.setdefault(key[len("labels.") :], doc.pop(key))
    labels: dict[str, str] = {}
    _flatten_labels(source, "", labels)
    labels = {k[:100]: v for k, v in list(labels.items())[:MAX_LABELS] if k}
    if labels:
        doc["labels"] = labels


_MESSAGE_FALLBACKS = ("body", "description", "summary", "details", "content", "shortmessage", "fullmessage")


def _normalize_message(doc: dict[str, Any], original: dict[str, Any]) -> None:
    value = doc.get("message")
    if _present(value) and not isinstance(value, str):
        value = _to_text(value)
    if not _present(value) or not str(value).strip():
        for key in list(doc):
            if _norm_key(key) in _MESSAGE_FALLBACKS and isinstance(doc[key], str) and doc[key].strip():
                value = doc.pop(key)
                break
        else:
            # Nothing reads like a message: the record itself is the message (nothing is lost).
            value = _to_text(original)
            add_tag(doc, TAG_MESSAGE_MISSING)
    if len(value) > MAX_MESSAGE_LENGTH:
        if not _present(doc.get("event.original")) and len(value) <= MAX_TEXT_LENGTH:
            doc["event.original"] = value
        value = value[:MAX_MESSAGE_LENGTH]
        add_tag(doc, TAG_TRUNCATED)
    doc["message"] = value


_SYSLOG_NAMES = ["emerg", "alert", "crit", "err", "warning", "notice", "info", "debug"]
_PINO_NAMES = {10: "trace", 20: "debug", 30: "info", 40: "warn", 50: "error", 60: "fatal"}


def numeric_level(value: int) -> str | None:
    """Syslog severity codes (0-7) and pino/bunyan levels (10-60)."""
    if 0 <= value <= 7:
        return _SYSLOG_NAMES[value]
    return _PINO_NAMES.get(value)


def _normalize_level(doc: dict[str, Any]) -> None:
    value = doc.get("log.level")
    if value is None:
        return
    if isinstance(value, bool):
        doc.pop("log.level")
        return
    if isinstance(value, int | float) or (isinstance(value, str) and value.strip().isdigit()):
        number = int(float(value))
        value = numeric_level(number) or str(number)
    text = _to_text(value).strip()
    # Google Cloud's DEFAULT means "no level": let the default apply.
    if not text or text.upper() == "DEFAULT":
        doc.pop("log.level")
    else:
        doc["log.level"] = text[:32]


def _normalize_timestamp(doc: dict[str, Any]) -> None:
    value = doc.get("@timestamp")
    if not _present(value):
        doc.pop("@timestamp", None)
        return
    iso = parse_timestamp(value)
    if iso:
        doc["@timestamp"] = iso
    else:
        doc.pop("@timestamp")
        add_tag(doc, TAG_TIMESTAMP_UNPARSED)
        add_label(doc, "original_timestamp", _to_text(value)[:200])


_ID = re.compile(r"[A-Za-z0-9-]{1,64}")


def sanitize_service_name(name: str) -> str:
    """ECS-friendly service name: lowercase letters, digits, '.', '_' and '-'."""
    cleaned = re.sub(r"[^a-z0-9._-]+", "-", name.strip().lower()).strip("-._")
    return cleaned[:100]


def _normalize_values(doc: dict[str, Any]) -> None:
    for field in ("trace.id", "span.id", "transaction.id"):
        if field in doc:
            value = _to_text(doc.pop(field)).strip()
            if _ID.fullmatch(value):
                doc[field] = value
            elif value:
                add_label(doc, field.replace(".", "_"), value)
    if "event.id" in doc:
        event_id = _to_text(doc.pop("event.id")).strip()[:128]
        if event_id:
            doc["event.id"] = event_id
    if "event.dataset" in doc:
        dataset = re.sub(r"[^A-Za-z0-9_.-]+", "_", _to_text(doc["event.dataset"])).strip("_")[:100]
        if dataset:
            doc["event.dataset"] = dataset
        else:
            doc.pop("event.dataset")
    if "ecs.version" in doc and not re.match(r"^\d+\.\d+\.\d+", _to_text(doc["ecs.version"])):
        doc.pop("ecs.version")
    if "service.name" in doc:
        original = _to_text(doc["service.name"])
        cleaned = sanitize_service_name(original)
        if cleaned != original.strip().lower():
            add_label(doc, "original_service_name", original)
        if cleaned:
            doc["service.name"] = cleaned
        else:
            doc.pop("service.name")
    for field, limit in _SHORT.items():
        if field in doc:
            doc[field] = _to_text(doc[field])[:limit]
    if "error.message" in doc:
        doc["error.message"] = _to_text(doc["error.message"])[:MAX_MESSAGE_LENGTH]
    if "error.stack_trace" in doc:
        stack = doc["error.stack_trace"]
        stack = "\n".join(map(_to_text, stack)) if isinstance(stack, list) else _to_text(stack)
        doc["error.stack_trace"] = stack[:MAX_TEXT_LENGTH]


def _fit_extra_fields(doc: dict[str, Any], original: dict[str, Any]) -> None:
    """Non-ECS fields are limited in size: drop the largest ones, and keep the whole record
    in event.original so they are not lost."""
    extra = {k: v for k, v in doc.items() if k not in CORE_FIELDS and k not in _OPAQUE}
    size = len(json.dumps(extra, ensure_ascii=False, default=str).encode())
    if size <= MAX_EXTRA_BYTES:
        return
    by_size = sorted(extra, key=lambda k: len(_to_text(extra[k])), reverse=True)
    for key in by_size:
        size -= len(json.dumps({key: extra[key]}, ensure_ascii=False, default=str).encode())
        doc.pop(key)
        if size <= MAX_EXTRA_BYTES:
            break
    add_tag(doc, TAG_FIELDS_DROPPED)
    if not _present(doc.get("event.original")):
        whole = _to_text(original)
        if len(whole) <= MAX_TEXT_LENGTH:
            doc["event.original"] = whole


def _normalize_original(doc: dict[str, Any]) -> None:
    if "event.original" in doc:
        original = _to_text(doc["event.original"])
        if len(original) > MAX_TEXT_LENGTH:
            original = original[:MAX_TEXT_LENGTH]
            add_tag(doc, TAG_TRUNCATED)
        doc["event.original"] = original


def normalize_document(record: dict[str, Any]) -> dict[str, Any]:
    """Any log record -> flat ECS document (dotted keys) ready for `LogCreate`."""
    doc = apply_aliases(flatten(record))
    _fit_object_slots(doc)
    _normalize_tags(doc)
    _normalize_labels(doc)
    _normalize_message(doc, record)
    _normalize_level(doc)
    _normalize_timestamp(doc)
    _normalize_values(doc)
    _normalize_original(doc)
    _fit_extra_fields(doc, record)
    return doc


def apply_defaults(doc: dict[str, Any], *, service_name: str | None, log_level: str | None) -> dict[str, Any]:
    """Fill service.name and log.level when the record has none."""
    if service_name and not has_field(doc, "service.name"):
        doc["service.name"] = sanitize_service_name(service_name) or service_name
    if not has_field(doc, "log.level"):
        doc["log.level"] = log_level or "info"
        add_tag(doc, TAG_LEVEL_MISSING)
    return doc
