"""Elastic Common Schema (ECS) support.

The ingestion endpoint accepts ECS log documents and the API returns them.

* Field names follow ECS (https://www.elastic.co/docs/reference/ecs).
* Input may use nested objects ({"log": {"level": "error"}}) or dotted keys
  ({"log.level": "error"}), as emitted by the ecs-logging libraries, or a mix of both.
  Output is always nested, as ECS recommends.
* `log.level` keeps the ORIGINAL value sent by the source ("WARN", "err", "Informational"),
  as ECS requires. Unknown spellings are accepted (tagged `_level_unrecognized`, counted as
  info) so no event is lost. For filtering and analysis we derive:
    - a normalized level (critical, error, warn, info, debug), stored internally;
    - `event.severity`, ECS's numeric severity. ECS leaves its meaning to the implementer;
      we use syslog severity codes (2 = critical ... 7 = debug), lower is more severe.
* Fields we do not model explicitly are kept as they came and returned untouched
  (ECS is a permissive schema: extra ECS fieldsets and custom fields are allowed).
"""

from collections.abc import Mapping
from typing import Any

ECS_VERSION = "9.4.0"

# Normalized level -> syslog-style severity code used for event.severity.
LEVEL_SEVERITY: dict[str, int] = {"critical": 2, "error": 3, "warn": 4, "info": 6, "debug": 7}
NORMALIZED_LEVELS = tuple(LEVEL_SEVERITY)

# Original level spelling (lowercased) -> (normalized level, event.severity).
# Covers syslog names, Python, Java (log4j/logback/JUL), Go, Node, .NET and Android styles.
_LEVEL_ALIASES: dict[str, tuple[str, int]] = {}
for _names, _level, _severity in [
    (("emerg", "emergency", "panic"), "critical", 0),
    (("alert",), "critical", 1),
    (("crit", "critical", "fatal", "severe", "f"), "critical", 2),
    (("err", "error", "e"), "error", 3),
    (("warn", "warning", "w"), "warn", 4),
    (("notice",), "info", 5),
    (("info", "informational", "information", "i"), "info", 6),
    (("debug", "trace", "verbose", "fine", "finer", "finest", "config", "d", "t", "v"), "debug", 7),
]:
    for _name in _names:
        _LEVEL_ALIASES[_name] = (_level, _severity)

ACCEPTED_LEVELS = sorted(_LEVEL_ALIASES)


def normalize_level(original: str) -> tuple[str, int]:
    """Map an original level spelling to (normalized level, event.severity)."""
    key = original.strip().lower()
    try:
        return _LEVEL_ALIASES[key]
    except KeyError:
        raise ValueError(
            f"unknown log level '{original}'. Accepted (case-insensitive): {', '.join(ACCEPTED_LEVELS)}"
        ) from None


# Levels nobody recognizes are stored, not rejected: losing the event is worse than a guess.
# Like Datadog, they count as info; the original stays in log.level and the log is tagged.
UNRECOGNIZED_LEVEL = ("info", 6)
TAG_LEVEL_UNRECOGNIZED = "_level_unrecognized"
# @timestamp was absent, so it was set to the ingestion time (ECS allows this).
TAG_TIMESTAMP_MISSING = "_timestamp_missing"


def normalize_level_lenient(original: str) -> tuple[str, int, bool]:
    """Like normalize_level, but falls back to info. Returns (level, severity, recognized)."""
    try:
        return (*normalize_level(original), True)
    except ValueError:
        return (*UNRECOGNIZED_LEVEL, False)


# --------------------------------------------------------------------- dotted <-> nested

# Objects whose keys must not be split on dots (ECS: labels is a flat key/value map).
OPAQUE_OBJECTS = frozenset({"labels"})


def nest(document: Mapping[str, Any]) -> dict[str, Any]:
    """Expand dotted keys into nested objects and merge both styles.

    {"log.level": "info", "log": {"logger": "x"}} -> {"log": {"level": "info", "logger": "x"}}
    Raises ValueError if the same field is given twice with different values.
    """
    result: dict[str, Any] = {}
    _merge_into(result, document, prefix=())
    return result


def _merge_into(target: dict[str, Any], source: Mapping[str, Any], prefix: tuple[str, ...]) -> None:
    for raw_key, value in source.items():
        if not isinstance(raw_key, str) or not raw_key:
            raise ValueError("field names must be non-empty strings")
        parts = (raw_key,) if prefix and prefix[0] in OPAQUE_OBJECTS else tuple(raw_key.split("."))
        if any(not p for p in parts):
            raise ValueError(f"invalid field name '{raw_key}'")
        _insert(target, parts, value, prefix)


def _insert(target: dict[str, Any], parts: tuple[str, ...], value: Any, prefix: tuple[str, ...]) -> None:
    head, rest = parts[0], parts[1:]
    path = (*prefix, head)
    if rest:
        child = target.setdefault(head, {})
        if not isinstance(child, dict):
            raise ValueError(f"field '{'.'.join(path)}' is both a value and an object")
        _insert(child, rest, value, path)
        return

    if isinstance(value, Mapping):
        child = target.setdefault(head, {})
        if not isinstance(child, dict):
            raise ValueError(f"field '{'.'.join(path)}' is both a value and an object")
        _merge_into(child, value, path)
        return

    if head in target and target[head] != value:
        raise ValueError(f"field '{'.'.join(path)}' is given twice with different values")
    target[head] = value


_MISSING = object()


def pop_path(document: dict[str, Any], path: str) -> Any:
    """Remove and return a dotted path from a nested document (or _MISSING)."""
    parts = path.split(".") if path != "@timestamp" else [path]
    node = document
    for part in parts[:-1]:
        node = node.get(part) if isinstance(node, dict) else None
        if not isinstance(node, dict):
            return _MISSING
    if not isinstance(node, dict) or parts[-1] not in node:
        return _MISSING
    value = node.pop(parts[-1])
    _prune_empty(document, parts[:-1])
    return value


def _prune_empty(document: dict[str, Any], parents: list[str]) -> None:
    for depth in range(len(parents), 0, -1):
        node = document
        for part in parents[: depth - 1]:
            node = node[part]
        if node.get(parents[depth - 1]) == {}:
            del node[parents[depth - 1]]
        else:
            break


def is_missing(value: Any) -> bool:
    return value is _MISSING


def deep_merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """Return base updated with override; nested dicts merge, override wins on conflicts."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged
