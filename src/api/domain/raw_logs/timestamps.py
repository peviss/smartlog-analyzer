"""Read the timestamp formats found in real logs and return ISO 8601."""

import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

# Plausible range for an epoch value: also decides its unit (s, ms, µs or ns).
_MIN_EPOCH = datetime(1990, 1, 1, tzinfo=UTC).timestamp()
_MAX_EPOCH = datetime(2100, 1, 1, tzinfo=UTC).timestamp()

_EPOCH = re.compile(r"\d{9,19}(?:\.\d+)?")
_ISO_LIKE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")
_FRACTION = re.compile(r"(\.\d{6})\d+")
_STRPTIME_FORMATS = (
    "%d/%b/%Y:%H:%M:%S %z",  # Apache / nginx: 16/Nov/2026:10:00:00 +0000
    "%Y/%m/%d %H:%M:%S",  # nginx error log, Go log
    "%Y/%m/%d %H:%M:%S.%f",
    "%b %d %Y %H:%M:%S",
    "%b %d, %Y %H:%M:%S",
    "%b %d, %Y %I:%M:%S %p",  # Java default: Nov 16, 2026 10:00:00 AM
    "%d-%b-%Y %H:%M:%S",
    "%d-%b-%Y %H:%M:%S.%f",  # Tomcat: 16-Nov-2026 10:00:00.123
    "%Y%m%dT%H%M%S%z",
    "%Y%m%dT%H%M%SZ",
)


def from_epoch(value: float) -> datetime | None:
    """Seconds, milliseconds, microseconds or nanoseconds since 1970, by magnitude."""
    for divisor in (1, 1e3, 1e6, 1e9):
        seconds = value / divisor
        if _MIN_EPOCH <= seconds <= _MAX_EPOCH:
            return datetime.fromtimestamp(seconds, UTC)
    return None


def parse_timestamp(value: Any) -> str | None:
    """ISO 8601 for a timestamp in any supported format, or None if it cannot be read.

    ISO input is returned as written (only made valid); other formats are converted.
    Values without a time zone stay naive: the schema treats them as UTC.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        parsed = from_epoch(value)
        return parsed.isoformat() if parsed else None
    if isinstance(value, datetime):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return None
    if _EPOCH.fullmatch(text):
        parsed = from_epoch(float(text))
        return parsed.isoformat() if parsed else None

    if _ISO_LIKE.match(text):
        iso = text.replace(",", ".")
        iso = iso[:10] + "T" + iso[11:]
        iso = re.sub(r"\s*UTC$", "Z", iso)
        iso = _FRACTION.sub(r"\1", iso)
        try:
            datetime.fromisoformat(iso)
            return iso
        except ValueError:
            pass

    cleaned = re.sub(r"\s*UTC$", " +0000", text)
    for fmt in _STRPTIME_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).isoformat()
        except ValueError:
            continue
    try:  # RFC 2822: Mon, 16 Nov 2026 10:00:00 +0000
        return parsedate_to_datetime(text).isoformat()
    except (TypeError, ValueError, IndexError):
        return None
