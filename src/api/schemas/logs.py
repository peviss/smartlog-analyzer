"""Log schemas: ECS input and output, filters and stats. Input validation lives here.

Logs follow the Elastic Common Schema (ECS); see api.domain.ecs for the conventions.
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from pydantic.json_schema import SkipJsonSchema, WithJsonSchema

from api.database.tables import LogEntry, LogLevel
from api.domain import ecs
from api.schemas.common import ID_PATTERN, Level, OptionalId, ServiceName, UtcDatetime

MAX_MESSAGE_LENGTH = 10_000
MAX_TEXT_LENGTH = 32_768
MAX_EXTRA_BYTES = 8_192
MAX_LABELS = 50
MAX_TAGS = 20
MAX_BATCH_SIZE = 1_000
CLOCK_SKEW_TOLERANCE = timedelta(minutes=5)

# ---------------------------------------------------------------- logs (ECS input)

# ECS path -> model field. Everything else in the document goes to `extra`.
ECS_FIELDS: dict[str, str] = {
    "@timestamp": "timestamp",
    "message": "message",
    "log.level": "log_level",
    "log.logger": "log_logger",
    "service.name": "service_name",
    "service.version": "service_version",
    "service.environment": "service_environment",
    "host.name": "host_name",
    "trace.id": "trace_id",
    "span.id": "span_id",
    "transaction.id": "transaction_id",
    "error.type": "error_type",
    "error.message": "error_message",
    "error.stack_trace": "error_stack_trace",
    "event.id": "event_id",
    "event.dataset": "event_dataset",
    "event.original": "event_original",
    "labels": "labels",
    "tags": "tags",
    "ecs.version": "ecs_version",
}
# Set by SmartLog, never taken from the client.
SERVER_MANAGED = ("event.ingested", "event.severity")
EXTRA_ALIAS = "__extra__"
RAW_ALIAS = "__raw__"


class LogCreate(BaseModel):
    """An ECS log document.

    Accepts nested objects (`{"log": {"level": "error"}}`), dotted keys
    (`{"log.level": "error"}`) or a mix. Required: `message`, `log.level`, `service.name`.
    Any other ECS or custom field is accepted and returned as sent.

    Send `event.id` to make retries safe: a log whose `event.id` is already stored for the
    same `service.name` is not stored again.
    """

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "additionalProperties": True,
            "examples": [
                {
                    "@timestamp": "2026-11-16T13:00:00Z",
                    "log.level": "ERROR",
                    "message": "Could not acquire database connection after 5000ms",
                    "service": {"name": "payments-api", "version": "1.4.2", "environment": "production"},
                    "host": {"name": "payments-api-2"},
                    "trace": {"id": "4bf92f3577b34da6a3ce929d0e0e4736"},
                    "labels": {"team": "payments"},
                    "ecs": {"version": ecs.ECS_VERSION},
                }
            ],
        },
    )

    timestamp: UtcDatetime | None = Field(
        default=None,
        alias="@timestamp",
        description="When the event happened. Defaults to ingestion time. Naive = UTC.",
    )
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_LENGTH)
    log_level: str = Field(
        alias="log.level",
        min_length=1,
        max_length=32,
        description="Original level as emitted by the source; stored as sent. Unknown spellings are "
        "accepted, counted as info and tagged `_level_unrecognized`.",
        examples=["ERROR", "warn", "Informational"],
    )
    log_logger: str | None = Field(default=None, alias="log.logger", max_length=256)
    service_name: ServiceName = Field(alias="service.name")
    service_version: str | None = Field(default=None, alias="service.version", max_length=64)
    service_environment: str | None = Field(default=None, alias="service.environment", max_length=64)
    host_name: str | None = Field(default=None, alias="host.name", max_length=255)
    trace_id: OptionalId = Field(default=None, alias="trace.id")
    span_id: OptionalId = Field(default=None, alias="span.id")
    transaction_id: OptionalId = Field(default=None, alias="transaction.id")
    error_type: str | None = Field(default=None, alias="error.type", max_length=256)
    error_message: str | None = Field(default=None, alias="error.message", max_length=MAX_MESSAGE_LENGTH)
    error_stack_trace: str | None = Field(default=None, alias="error.stack_trace", max_length=MAX_TEXT_LENGTH)
    event_id: str | None = Field(
        default=None,
        alias="event.id",
        min_length=1,
        max_length=128,
        description="Unique id of the event at the source. Makes retries idempotent per service.name.",
    )
    event_dataset: str | None = Field(
        default=None, alias="event.dataset", pattern=r"^[A-Za-z0-9_.-]{1,100}$", examples=["payments-api.app"]
    )
    event_original: str | None = Field(default=None, alias="event.original", max_length=MAX_TEXT_LENGTH)
    labels: dict[str, str | int | float | bool] = Field(default_factory=dict, max_length=MAX_LABELS)
    tags: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(
        default_factory=list, max_length=MAX_TAGS
    )
    ecs_version: str | None = Field(default=None, alias="ecs.version", pattern=r"^\d+\.\d+\.\d+")
    extra: SkipJsonSchema[dict[str, Any]] = Field(default_factory=dict, alias=EXTRA_ALIAS)
    # The document exactly as received (compact JSON); stored only if INGEST_KEEP_ORIGINAL is on.
    raw: SkipJsonSchema[str | None] = Field(default=None, alias=RAW_ALIAS, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def from_ecs_document(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)
        document = ecs.nest(data)  # ValueError -> 422 with a clear message
        flat: dict[str, Any] = {RAW_ALIAS: raw}
        for path in ECS_FIELDS:
            value = ecs.pop_path(document, path)
            if not ecs.is_missing(value):
                flat[path] = value
        for path in SERVER_MANAGED:
            ecs.pop_path(document, path)
        flat[EXTRA_ALIAS] = document
        return flat

    @field_validator("message")
    @classmethod
    def message_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("message must not be blank")
        return v.rstrip()

    @field_validator("log_level")
    @classmethod
    def level_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("log.level must not be blank")
        return v.strip()

    @field_validator("timestamp")
    @classmethod
    def timestamp_not_in_future(cls, v: datetime | None) -> datetime | None:
        if v is not None and v > datetime.now(UTC) + CLOCK_SKEW_TOLERANCE:
            raise ValueError("@timestamp is in the future")
        return v

    @field_validator("labels")
    @classmethod
    def labels_are_keywords(cls, v: dict[str, Any]) -> dict[str, str]:
        # ECS: labels are flat and every value is stored as a keyword (string).
        for key in v:
            if not key or len(key) > 100:
                raise ValueError("label names must be 1-100 characters")
        return {k: str(val).lower() if isinstance(val, bool) else str(val) for k, val in v.items()}

    @field_validator("extra")
    @classmethod
    def extra_size(cls, v: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(v, default=str).encode()) > MAX_EXTRA_BYTES:
            raise ValueError(f"additional (non-core) fields must be at most {MAX_EXTRA_BYTES} bytes in total")
        return v

    @property
    def normalized_level(self) -> tuple[str, int]:
        level, severity, _ = ecs.normalize_level_lenient(self.log_level)
        return level, severity

    def to_row(self, *, keep_original: bool = False) -> dict[str, Any]:
        level, severity, recognized = ecs.normalize_level_lenient(self.log_level)
        # Processing marks, Elastic style (`_grokparsefailure`): what SmartLog had to assume.
        marks = [] if recognized else [ecs.TAG_LEVEL_UNRECOGNIZED]
        if self.timestamp is None:
            marks.append(ecs.TAG_TIMESTAMP_MISSING)
        original = self.event_original
        if original is None and keep_original and self.raw and len(self.raw) <= MAX_TEXT_LENGTH:
            original = self.raw
        row = {field: getattr(self, field) for field in ECS_FIELDS.values()}
        row.update(
            timestamp=self.timestamp or datetime.now(UTC),
            level=level,
            event_severity=severity,
            event_original=original,
            tags=self.tags + [m for m in marks if m not in self.tags],
            extra=self.extra,
        )
        return row


# Batch items are validated one by one (see routers/logs.py), so one bad log does not
# reject the rest. The schema still documents each item as a LogCreate.
BatchItem = Annotated[Any, WithJsonSchema({"$ref": "#/components/schemas/LogCreate"})]


class LogBatchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    logs: list[BatchItem] = Field(min_length=1, max_length=MAX_BATCH_SIZE)


# ---------------------------------------------------------------- logs (ECS output)


class _Open(BaseModel):
    """ECS objects keep any extra field the client sent (ECS is permissive)."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class EcsLog(_Open):
    level: str = Field(description="Original level as sent by the source")
    logger: str | None = None


class EcsService(_Open):
    name: str
    version: str | None = None
    environment: str | None = None


class EcsHost(_Open):
    name: str | None = None


class EcsId(_Open):
    id: str | None = None


class EcsError(_Open):
    type: str | None = None
    message: str | None = None
    stack_trace: str | None = None


class EcsEvent(_Open):
    severity: int = Field(description="Derived by SmartLog: 2 critical, 3 error, 4 warn, 5-6 info, 7 debug")
    ingested: datetime = Field(description="When SmartLog received the event")
    id: str | None = None
    dataset: str | None = None
    original: str | None = None


class EcsMeta(_Open):
    version: str


class LogRead(_Open):
    """A stored log as an ECS document. `id` is SmartLog's resource id, not an ECS field."""

    # ecs-logging convention: @timestamp, log.level and message come first.
    id: int
    timestamp: datetime = Field(alias="@timestamp")
    log: EcsLog
    message: str
    service: EcsService
    event: EcsEvent
    ecs: EcsMeta
    host: EcsHost | None = None
    trace: EcsId | None = None
    span: EcsId | None = None
    transaction: EcsId | None = None
    error: EcsError | None = None
    labels: dict[str, str] | None = None
    tags: list[str] | None = None


def _compact(d: dict[str, Any]) -> dict[str, Any] | None:
    cleaned = {k: v for k, v in d.items() if v not in (None, {}, [])}
    return cleaned or None


def to_ecs_document(entry: LogEntry) -> dict[str, Any]:
    """Rebuild the nested ECS document for a stored log."""
    core = {
        "@timestamp": entry.timestamp,
        "message": entry.message,
        "log": _compact({"level": entry.log_level, "logger": entry.log_logger}),
        "service": _compact(
            {
                "name": entry.service_name,
                "version": entry.service_version,
                "environment": entry.service_environment,
            }
        ),
        "host": _compact({"name": entry.host_name}),
        "trace": _compact({"id": entry.trace_id}),
        "span": _compact({"id": entry.span_id}),
        "transaction": _compact({"id": entry.transaction_id}),
        "error": _compact(
            {"type": entry.error_type, "message": entry.error_message, "stack_trace": entry.error_stack_trace}
        ),
        "event": _compact(
            {
                "severity": entry.event_severity,
                "ingested": entry.ingested_at,
                "id": entry.event_id,
                "dataset": entry.event_dataset,
                "original": entry.event_original,
            }
        ),
        "labels": entry.labels or None,
        "tags": entry.tags or None,
        "ecs": {"version": entry.ecs_version or ecs.ECS_VERSION},
    }
    core = {k: v for k, v in core.items() if v is not None}
    # Extra fields first, core fields win on any conflict.
    document = ecs.deep_merge(entry.extra or {}, core)
    document["id"] = entry.id
    return document


def to_log_read(entry: LogEntry) -> LogRead:
    return LogRead.model_validate(to_ecs_document(entry))


class ValidationErrorDetail(BaseModel):
    """Same shape as the `details` of a VALIDATION_ERROR response."""

    field: str | None
    location: str | None
    message: str | None
    type: str | None


class LogBatchItemError(BaseModel):
    index: int = Field(description="Position of the rejected log in the request (0-based).")
    line: int | None = Field(
        default=None, description="Imports only: line of the file where the record starts (1-based)."
    )
    errors: list[ValidationErrorDetail]


class LogBatchResult(BaseModel):
    ingested: int = Field(description="Logs stored by this request.")
    ids: list[int] = Field(description="Ids of the stored logs, in request order.")
    duplicates: int = Field(
        default=0, description="Logs skipped because their event.id was already stored (retries)."
    )
    failed: int = Field(default=0, description="Logs rejected by validation; see `errors`.")
    errors: list[LogBatchItemError] = Field(default_factory=list)


class LogImportResult(LogBatchResult):
    format: Literal["text", "json", "csv"] = Field(description="How the input was parsed.")
    records: int = Field(description="Records found in the input (blank lines and empty rows excluded).")


class PageMeta(BaseModel):
    total: int
    limit: int
    offset: int
    has_more: bool


class LogPage(BaseModel):
    items: list[LogRead]
    page: PageMeta


class LogFilters(BaseModel):
    """Shared filter set (ECS field names) used by GET /api/logs, stats and analysis requests."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    service_name: list[ServiceName] | None = Field(default=None, alias="service.name")
    level: list[Level] | None = Field(
        default=None,
        alias="log.level",
        description="Matches the normalized level, so `warn` also matches WARN/warning/W. "
        "Values: critical, error, warn, info, debug (any accepted spelling).",
    )
    service_environment: str | None = Field(default=None, alias="service.environment", max_length=64)
    host_name: str | None = Field(default=None, alias="host.name", max_length=255)
    event_dataset: str | None = Field(default=None, alias="event.dataset", max_length=100)
    trace_id: str | None = Field(default=None, alias="trace.id", pattern=ID_PATTERN)
    from_: UtcDatetime | None = Field(default=None, alias="from", description="@timestamp >= from")
    to: UtcDatetime | None = Field(default=None, description="@timestamp <= to")
    q: str | None = Field(default=None, min_length=1, max_length=200, description="Text search in message")

    @model_validator(mode="after")
    def check_range(self):
        if self.from_ and self.to and self.from_ > self.to:
            raise ValueError("'from' must be earlier than or equal to 'to'")
        return self


class LogListQuery(LogFilters):
    """Query string of GET /api/logs: filters plus pagination."""

    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0, le=1_000_000)


# ---------------------------------------------------------------- stats

GroupBy = Literal["service.name", "log.level", "service.environment", "host.name", "hour", "day"]


class StatsQuery(LogFilters):
    """Query string of GET /api/logs/stats."""

    group_by: list[GroupBy] = Field(default=["service.name", "log.level"], min_length=1)

    @field_validator("group_by")
    @classmethod
    def normalize_group_by(cls, v: list[GroupBy]) -> list[GroupBy]:
        """Drop duplicates (keeping order); `hour` already implies `day`."""
        v = list(dict.fromkeys(v))
        if "hour" in v and "day" in v:
            v.remove("day")
        return v


class StatsBucket(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    service_name: str | None = Field(default=None, alias="service.name")
    level: LogLevel | None = Field(default=None, alias="log.level", description="Normalized level")
    service_environment: str | None = Field(default=None, alias="service.environment")
    host_name: str | None = Field(default=None, alias="host.name")
    period: datetime | None = None
    count: int
    first_seen: datetime
    last_seen: datetime


class StatsResponse(BaseModel):
    group_by: list[GroupBy]
    total: int
    buckets: list[StatsBucket]
