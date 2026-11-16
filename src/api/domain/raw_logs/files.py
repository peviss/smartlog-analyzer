"""Whole inputs (plain text, JSON, CSV) -> normalized records with their line numbers."""

import csv
import io
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from api.domain.raw_logs import profiles
from api.domain.raw_logs.normalize import MAX_TEXT_LENGTH, has_field, normalize_document
from api.domain.raw_logs.text import line_records

ImportFormat = Literal["text", "json", "csv"]

MAX_IMPORT_BYTES = 5 * 1024 * 1024
MAX_IMPORT_RECORDS = 10_000

# Indented lines and "Caused by:" continue the previous record (stack traces).
_CONTINUATION = re.compile(r"^(?:\s+\S|Caused by:)")


@dataclass
class RawRecord:
    """One record found in the input, before validation."""

    line: int | None  # 1-based line where the record starts; None when unknown (JSON array)
    doc: Any = None  # normalized ECS document, or a non-object left for validation to reject
    error: str | None = None  # the record could not be parsed at all
    continuation: list[str] = field(default_factory=list)
    raw_lines: list[str] = field(default_factory=list)


def _from_json_value(value: Any, line: int | None) -> list[RawRecord]:
    if not isinstance(value, dict):
        return [RawRecord(line, value)]
    return [RawRecord(line, normalize_document(doc)) for doc in profiles.convert(value)]


def parse_json(text: str) -> list[RawRecord]:
    """A JSON array, `{"logs": [...]}`, one object (any known schema), or NDJSON."""
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return parse_ndjson(text)
    if isinstance(value, dict) and set(value) == {"logs"} and isinstance(value["logs"], list):
        value = value["logs"]
    if isinstance(value, list):
        return [record for item in value for record in _from_json_value(item, None)]
    return _from_json_value(value, 1)


def parse_ndjson(text: str) -> list[RawRecord]:
    records: list[RawRecord] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            records.append(RawRecord(number, error=f"invalid JSON: {exc.msg}"))
            continue
        records += _from_json_value(value, number)
    return records


def parse_csv(text: str) -> list[RawRecord]:
    """First row is the header; any column names (ECS or not) are normalized."""
    first_line = text.split("\n", 1)[0]
    try:
        dialect: Any = csv.Sniffer().sniff(first_line, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        return []
    records = []
    start = reader.line_num + 1
    for row in reader:
        if None in row:
            records.append(RawRecord(start, error="the row has more columns than the header"))
        elif any(v and v.strip() for v in row.values()):
            doc = {k.strip(): v.strip() for k, v in row.items() if k and v is not None and v.strip()}
            records.append(RawRecord(start, normalize_document(doc)))
        start = reader.line_num + 1
    return records


def parse_text(text: str, now: datetime | None = None) -> list[RawRecord]:
    """One record per line (see raw_logs.text), plus stack-trace continuation lines.
    Each record keeps its raw line(s) in event.original."""
    records: list[RawRecord] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        last = records[-1] if records else None
        if last and last.raw_lines and _CONTINUATION.match(line):
            last.continuation.append(line.rstrip())
            last.raw_lines.append(line.rstrip())
            continue
        docs = line_records(line, now)
        single = len(docs) == 1
        records += [RawRecord(number, doc, raw_lines=[line.rstrip()] if single else []) for doc in docs]

    for record in records:
        doc = record.doc
        if record.continuation and not has_field(doc, "error.stack_trace"):
            doc["error.stack_trace"] = "\n".join(record.continuation)
        raw = "\n".join(record.raw_lines)
        if raw and len(raw) <= MAX_TEXT_LENGTH and not has_field(doc, "event.original"):
            doc["event.original"] = raw
        record.doc = normalize_document(doc)
    return records


def detect_format(content_type: str | None, explicit: ImportFormat | None) -> ImportFormat:
    if explicit:
        return explicit
    media = (content_type or "").split(";", 1)[0].strip().lower()
    if media in {"text/csv", "application/csv"}:
        return "csv"
    if media in {"application/json", "application/x-ndjson", "application/ndjson", "application/jsonl"}:
        return "json"
    return "text"


PARSERS = {"text": parse_text, "json": parse_json, "csv": parse_csv}


def parse(text: str, fmt: ImportFormat) -> list[RawRecord]:
    return PARSERS[fmt](text)
