"""Use case: validate many log documents one by one and store the valid ones.

Shared by the batch endpoint and the file import: one bad record never rejects the rest,
and every rejection says where it was (position and, for files, line).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.errors import BatchValidationError, clean_validation_errors
from api.repositories.logs import LogRepository
from api.schemas.logs import LogBatchItemError, LogBatchResult, LogCreate, ValidationErrorDetail

INSERT_CHUNK = 1_000  # rows per INSERT statement


@dataclass
class IngestItem:
    doc: Any
    line: int | None = None
    parse_error: str | None = None  # the record could not even be parsed


class IngestionService:
    def __init__(self, session: AsyncSession, *, keep_original: bool) -> None:
        self.session = session
        self.keep_original = keep_original
        self.logs = LogRepository(session)

    def _validate(
        self, items: Sequence[IngestItem], loc_prefix: tuple[str | int, ...]
    ) -> tuple[list[LogCreate], list[LogBatchItemError]]:
        valid: list[LogCreate] = []
        errors: list[LogBatchItemError] = []
        for index, item in enumerate(items):
            prefix = (*loc_prefix, index) if loc_prefix else ()
            if item.parse_error:
                detail = ValidationErrorDetail(
                    field=None, location="body", message=item.parse_error, type="parse_error"
                )
                errors.append(LogBatchItemError(index=index, line=item.line, errors=[detail]))
                continue
            try:
                valid.append(LogCreate.model_validate(item.doc))
            except ValidationError as exc:
                located = [{**e, "loc": ("body", *prefix, *e["loc"])} for e in exc.errors()]
                errors.append(
                    LogBatchItemError(index=index, line=item.line, errors=clean_validation_errors(located))
                )
        return valid, errors

    async def ingest(
        self, items: Sequence[IngestItem], *, loc_prefix: tuple[str | int, ...] = ()
    ) -> LogBatchResult:
        """Validate and store. `loc_prefix` places field errors in the request, e.g. ("logs",)
        gives `logs.3.message`. Raises BatchValidationError (422) if no record is valid."""
        valid, errors = self._validate(items, loc_prefix)
        if not valid:
            details = [
                {**detail.model_dump(), **({"line": error.line} if error.line else {})}
                for error in errors
                for detail in error.errors
            ]
            raise BatchValidationError("No valid log in the request.", details)
        ids: list[int] = []
        # One transaction: either every valid record is stored or none is.
        for start in range(0, len(valid), INSERT_CHUNK):
            chunk = valid[start : start + INSERT_CHUNK]
            ids += await self.logs.insert_many(chunk, keep_original=self.keep_original)
        await self.session.commit()
        return LogBatchResult(
            ingested=len(ids),
            ids=ids,
            duplicates=len(valid) - len(ids),
            failed=len(errors),
            errors=errors,
        )
