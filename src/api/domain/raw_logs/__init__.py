"""Turn raw logs (plain text, JSON, CSV) in any schema into ECS documents.

* `profiles`: known JSON schemas (OpenTelemetry, GELF, Serilog CLEF, Google Cloud,
  CloudWatch, Docker), translated with their own semantics.
* `text`: plain-text line formats (syslog, access logs, klog, CRI, logfmt, generic).
* `normalize`: the generic mapping every record goes through (field names, value formats,
  size limits); it moves what does not fit instead of rejecting the record.
* `files`: whole inputs, with line numbers and stack-trace continuation lines.

Every record is still validated by `LogCreate` afterwards.
"""

from api.domain.raw_logs.files import (
    MAX_IMPORT_BYTES,
    MAX_IMPORT_RECORDS,
    ImportFormat,
    RawRecord,
    detect_format,
    parse,
    parse_csv,
    parse_json,
    parse_ndjson,
    parse_text,
)
from api.domain.raw_logs.normalize import (
    TAG_FIELDS_DROPPED,
    TAG_LEVEL_MISSING,
    TAG_MESSAGE_MISSING,
    TAG_TIMESTAMP_UNPARSED,
    TAG_TRUNCATED,
    apply_aliases,
    apply_defaults,
    normalize_document,
)
from api.domain.raw_logs.profiles import detect_profile
from api.domain.raw_logs.text import parse_line

__all__ = [
    "MAX_IMPORT_BYTES",
    "MAX_IMPORT_RECORDS",
    "TAG_FIELDS_DROPPED",
    "TAG_LEVEL_MISSING",
    "TAG_MESSAGE_MISSING",
    "TAG_TIMESTAMP_UNPARSED",
    "TAG_TRUNCATED",
    "ImportFormat",
    "RawRecord",
    "apply_aliases",
    "apply_defaults",
    "detect_format",
    "detect_profile",
    "normalize_document",
    "parse",
    "parse_csv",
    "parse_json",
    "parse_line",
    "parse_ndjson",
    "parse_text",
]
