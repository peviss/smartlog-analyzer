"""Log ingestion, search and stats."""

from typing import Annotated

from fastapi import APIRouter, Path, Query, Request, Response, status

from api.core.errors import InvalidImportError, NotFoundError, PayloadTooLargeError
from api.domain import raw_logs
from api.domain.raw_logs import MAX_IMPORT_BYTES, MAX_IMPORT_RECORDS, ImportFormat
from api.repositories.logs import LogRepository
from api.routers.deps import SessionDep, SettingsDep
from api.schemas.logs import (
    LogBatchCreate,
    LogBatchResult,
    LogCreate,
    LogImportResult,
    LogListQuery,
    LogPage,
    LogRead,
    PageMeta,
    StatsBucket,
    StatsQuery,
    StatsResponse,
    to_log_read,
)
from api.services.ingestion import IngestionService, IngestItem

router = APIRouter(prefix="/logs", tags=["logs"])

ERROR_RESPONSES = {422: {"description": "Validation error"}}


@router.post(
    "",
    response_model=LogRead,
    response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
    summary="Ingest an ECS log document",
    responses={
        200: {"model": LogRead, "description": "Retry: a log with this event.id was already stored"},
        **ERROR_RESPONSES,
    },
)
async def ingest_log(
    payload: LogCreate, session: SessionDep, settings: SettingsDep, response: Response
) -> LogRead:
    entry, created = await LogRepository(session).create(payload, keep_original=settings.ingest_keep_original)
    if not created:
        response.status_code = status.HTTP_200_OK
    return to_log_read(entry)


@router.post(
    "/batch",
    response_model=LogBatchResult,
    status_code=status.HTTP_201_CREATED,
    summary="Ingest up to 1000 ECS log documents in one request",
    description=(
        "Each log is validated on its own: valid logs are stored and invalid ones are reported in "
        "`errors` with their position, so one bad log does not reject the rest. Logs whose `event.id` "
        "is already stored for the same `service.name` are counted in `duplicates` and not stored "
        "again. If no log is valid the response is 422."
    ),
    responses={422: {"description": "Invalid body, or no valid log in the batch"}},
)
async def ingest_batch(payload: LogBatchCreate, session: SessionDep, settings: SettingsDep) -> LogBatchResult:
    service = IngestionService(session, keep_original=settings.ingest_keep_original)
    return await service.ingest([IngestItem(doc) for doc in payload.logs], loc_prefix=("logs",))


IMPORT_BODY = {
    "required": True,
    "content": {
        "text/plain": {"schema": {"type": "string"}, "example": "2026-11-16T10:00:00Z ERROR Payment failed"},
        "text/csv": {
            "schema": {"type": "string"},
            "example": "@timestamp,log.level,service.name,message\n2026-11-16T10:00:00Z,ERROR,payments,boom",
        },
        "application/json": {"schema": {}},
        "application/x-ndjson": {"schema": {"type": "string"}},
    },
}


@router.post(
    "/import",
    response_model=LogImportResult,
    status_code=status.HTTP_201_CREATED,
    summary="Import raw logs: plain text, JSON or CSV",
    description=(
        "The body is the file content. The format comes from `format`, or else from the Content-Type "
        "(`text/plain`, `application/json` or `application/x-ndjson`, `text/csv`).\n\n"
        "Records do not need to be ECS: they are parsed and mapped to ECS.\n\n"
        "* **text**: one record per line. Recognizes NDJSON, syslog (RFC 5424 and 3164), Apache/nginx "
        "access logs, klog, Kubernetes CRI, logfmt, and `<timestamp> <LEVEL> <message>` (Python logging, "
        "logback, log4j: also the logger and thread). Indented and `Caused by:` lines are added to the "
        "previous record as `error.stack_trace`. The raw line is kept in `event.original`.\n"
        '* **json**: an array, `{"logs": [...]}`, one object, or NDJSON. Known schemas are translated: '
        "OpenTelemetry (OTLP/JSON), GELF, Serilog CLEF, Google Cloud Logging, AWS CloudWatch, Docker json-file.\n"
        "* **csv**: header row with any column names; `,` `;` tab or `|`.\n\n"
        "Any other schema is normalized: nested objects are flattened, names in any style (`traceId`, "
        "`trace_id`) are mapped to ECS, epoch and common date formats are read, a plain value where ECS has "
        'an object (`"error": "..."`) is moved to its main field, invalid service names and ids are '
        "fixed or moved to labels. What could not be kept as is is marked with a tag (`_message_missing`, "
        "`_timestamp_unparsed`, `_truncated`, `_fields_dropped`, `_level_missing`) or a label "
        "(`original_service_name`, `original_timestamp`). `service.name` and `log.level` fill in records "
        "that lack them. Like the batch endpoint, valid records are stored and invalid ones are reported "
        f"with their line. Up to {MAX_IMPORT_RECORDS} records and {MAX_IMPORT_BYTES // 1024 // 1024} MB; "
        "the file must be UTF-8."
    ),
    openapi_extra={"requestBody": IMPORT_BODY},
    responses={
        413: {"description": "File too large"},
        422: {"description": "Unreadable file, too many records, or no valid record"},
    },
)
async def import_logs(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    format_: Annotated[
        ImportFormat | None, Query(alias="format", description="Overrides the Content-Type.")
    ] = None,
    service_name: Annotated[
        str | None, Query(alias="service.name", max_length=100, description="For records without one.")
    ] = None,
    log_level: Annotated[
        str | None,
        Query(alias="log.level", max_length=32, description="For records without one (default info)."),
    ] = None,
) -> LogImportResult:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_IMPORT_BYTES:
        raise PayloadTooLargeError(f"The file is larger than {MAX_IMPORT_BYTES // 1024 // 1024} MB.")
    body = await request.body()
    if len(body) > MAX_IMPORT_BYTES:
        raise PayloadTooLargeError(f"The file is larger than {MAX_IMPORT_BYTES // 1024 // 1024} MB.")
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InvalidImportError("The file must be UTF-8 text.") from exc

    fmt = raw_logs.detect_format(request.headers.get("content-type"), format_)
    records = raw_logs.parse(text, fmt)
    if not records:
        raise InvalidImportError("No log records found in the input.")
    if len(records) > MAX_IMPORT_RECORDS:
        raise InvalidImportError(f"Too many records ({len(records)}); the limit is {MAX_IMPORT_RECORDS}.")

    items = [
        IngestItem(
            raw_logs.apply_defaults(r.doc, service_name=service_name, log_level=log_level)
            if isinstance(r.doc, dict)
            else r.doc,
            line=r.line,
            parse_error=r.error,
        )
        for r in records
    ]
    service = IngestionService(session, keep_original=settings.ingest_keep_original)
    result = await service.ingest(items)
    return LogImportResult(**result.model_dump(), format=fmt, records=len(records))


@router.get(
    "",
    response_model=LogPage,
    response_model_exclude_none=True,
    summary="List logs (as ECS documents) with filters",
    responses=ERROR_RESPONSES,
)
async def list_logs(query: Annotated[LogListQuery, Query()], session: SessionDep) -> LogPage:
    items, total = await LogRepository(session).search(query, query.limit, query.offset)
    return LogPage(
        items=[to_log_read(i) for i in items],
        page=PageMeta(
            total=total, limit=query.limit, offset=query.offset, has_more=query.offset + len(items) < total
        ),
    )


@router.get(
    "/stats",
    response_model=StatsResponse,
    response_model_exclude_none=True,
    summary="Aggregated counts by service, level and/or time bucket",
    responses=ERROR_RESPONSES,
)
async def log_stats(query: Annotated[StatsQuery, Query()], session: SessionDep) -> StatsResponse:
    rows = await LogRepository(session).stats(query, query.group_by)
    return StatsResponse(
        group_by=query.group_by,
        total=sum(r["count"] for r in rows),
        buckets=[StatsBucket(**r) for r in rows],
    )


@router.get(
    "/{log_id}",
    response_model=LogRead,
    response_model_exclude_none=True,
    summary="Get one log (as an ECS document) by id",
    responses={404: {"description": "Log not found"}, 422: {"description": "Invalid id"}},
)
async def get_log(log_id: Annotated[int, Path(ge=1)], session: SessionDep) -> LogRead:
    entry = await LogRepository(session).get(log_id)
    if entry is None:
        raise NotFoundError(f"Log {log_id} not found.")
    return to_log_read(entry)
