"""Annotated types shared by the request/response schemas."""

from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    BeforeValidator,
    Field,
)

from api.database.tables import LogLevel
from api.domain import ecs

ID_PATTERN = r"^[A-Za-z0-9-]{1,64}$"


def _ensure_utc(value: datetime) -> datetime:
    """Naive datetimes are interpreted as UTC; aware ones are converted to UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _to_normalized_level(value: Any) -> Any:
    """Accept any known spelling ("WARN", "warning", "err"...) in filters."""
    if isinstance(value, str):
        try:
            return ecs.normalize_level(value)[0]
        except ValueError:
            return value  # let the enum validation produce the error
    return value


Level = Annotated[LogLevel, BeforeValidator(_to_normalized_level)]
UtcDatetime = Annotated[datetime, AfterValidator(_ensure_utc)]
ServiceName = Annotated[
    str,
    BeforeValidator(lambda v: v.strip().lower() if isinstance(v, str) else v),
    Field(
        min_length=1,
        max_length=100,
        pattern=r"^[a-z0-9][a-z0-9._-]*$",
        description="Service name: lowercase letters, digits, '.', '_' or '-'.",
        examples=["payments-api"],
    ),
]
OptionalId = Annotated[str | None, Field(default=None, pattern=ID_PATTERN)]
