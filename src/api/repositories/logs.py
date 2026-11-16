"""Data access for logs, including the aggregation query."""

from sqlalchemy import Select, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from api.database.tables import LogEntry
from api.schemas.logs import GroupBy, LogCreate, LogFilters

# group_by value (ECS name) -> (column, result key)
GROUP_COLUMNS = {
    "service.name": (LogEntry.service_name, "service_name"),
    "log.level": (LogEntry.level, "level"),
    "service.environment": (LogEntry.service_environment, "service_environment"),
    "host.name": (LogEntry.host_name, "host_name"),
}


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def apply_filters(stmt: Select, filters: LogFilters) -> Select:
    if filters.service_name:
        stmt = stmt.where(LogEntry.service_name.in_(filters.service_name))
    if filters.level:
        stmt = stmt.where(LogEntry.level.in_([lvl.value for lvl in filters.level]))
    if filters.service_environment:
        stmt = stmt.where(LogEntry.service_environment == filters.service_environment)
    if filters.host_name:
        stmt = stmt.where(LogEntry.host_name == filters.host_name)
    if filters.event_dataset:
        stmt = stmt.where(LogEntry.event_dataset == filters.event_dataset)
    if filters.from_:
        stmt = stmt.where(LogEntry.timestamp >= filters.from_)
    if filters.to:
        stmt = stmt.where(LogEntry.timestamp <= filters.to)
    if filters.trace_id:
        stmt = stmt.where(LogEntry.trace_id == filters.trace_id)
    if filters.q:
        stmt = stmt.where(LogEntry.message.ilike(f"%{_escape_like(filters.q)}%", escape="\\"))
    return stmt


class LogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _insert_ignoring_retries():
        """INSERT that skips logs whose (service.name, event.id) is already stored."""
        return pg_insert(LogEntry).on_conflict_do_nothing(
            index_elements=[LogEntry.service_name, LogEntry.event_id],
            index_where=LogEntry.event_id.is_not(None),
        )

    async def create(self, log: LogCreate, *, keep_original: bool = False) -> tuple[LogEntry, bool]:
        """Store one log. Returns (entry, created); created is False for a retried event.id."""
        stmt = self._insert_ignoring_retries().values(**log.to_row(keep_original=keep_original))
        entry = (await self.session.execute(stmt.returning(LogEntry))).scalar_one_or_none()
        created = entry is not None
        if entry is None:
            entry = await self.session.scalar(
                select(LogEntry).where(
                    LogEntry.service_name == log.service_name, LogEntry.event_id == log.event_id
                )
            )
        await self.session.commit()
        await self.session.refresh(entry)
        return entry, created

    async def insert_many(self, logs: list[LogCreate], *, keep_original: bool = False) -> list[int]:
        """Insert logs in one statement without committing. Returns the ids of the ones stored
        (retries are skipped), in input order."""
        result = await self.session.execute(
            self._insert_ignoring_retries().returning(LogEntry.id, sort_by_parameter_order=True),
            [log.to_row(keep_original=keep_original) for log in logs],
        )
        return list(result.scalars())

    async def create_many(self, logs: list[LogCreate], *, keep_original: bool = False) -> list[int]:
        """Store logs in one statement and commit."""
        ids = await self.insert_many(logs, keep_original=keep_original)
        await self.session.commit()
        return ids

    async def search(self, filters: LogFilters, limit: int, offset: int) -> tuple[list[LogEntry], int]:
        base = apply_filters(select(LogEntry), filters)
        total = await self.session.scalar(select(func.count()).select_from(base.subquery()))
        rows = await self.session.scalars(
            base.order_by(LogEntry.timestamp.desc(), LogEntry.id.desc()).limit(limit).offset(offset)
        )
        return list(rows), int(total or 0)

    async def get(self, log_id: int) -> LogEntry | None:
        return await self.session.get(LogEntry, log_id)

    async def get_many(self, ids: list[int]) -> list[LogEntry]:
        rows = await self.session.scalars(select(LogEntry).where(LogEntry.id.in_(ids)))
        return list(rows)

    async def recent_matching(self, filters: LogFilters, limit: int) -> list[LogEntry]:
        stmt = apply_filters(select(LogEntry), filters)
        rows = await self.session.scalars(
            stmt.order_by(LogEntry.timestamp.desc(), LogEntry.id.desc()).limit(limit)
        )
        return list(rows)

    async def stats(self, filters: LogFilters, group_by: list[GroupBy]) -> list[dict]:
        """Aggregation: COUNT / MIN / MAX grouped by any combination of
        service.name, log.level (normalized), service.environment, host.name
        and a time bucket (hour or day).

        Example SQL for group_by=[service.name, log.level, hour]:

            SELECT service_name, level, date_trunc('hour', timestamp) AS period,
                   count(*), min(timestamp), max(timestamp)
            FROM log_entries WHERE ...
            GROUP BY service_name, level, period
            ORDER BY period DESC, count DESC
        """
        keys = [GROUP_COLUMNS[g] for g in group_by if g in GROUP_COLUMNS]
        exprs = [column.label(key) for column, key in keys]
        bucket = "hour" if "hour" in group_by else "day" if "day" in group_by else None
        period = func.date_trunc(bucket, LogEntry.timestamp).label("period") if bucket else None
        group_exprs = [column for column, _ in keys] + ([period] if period is not None else [])

        count = func.count().label("count")
        stmt = select(
            *exprs,
            *([period] if period is not None else []),
            count,
            func.min(LogEntry.timestamp).label("first_seen"),
            func.max(LogEntry.timestamp).label("last_seen"),
        )
        stmt = apply_filters(stmt, filters).group_by(*group_exprs)
        order = ([period.desc()] if period is not None else []) + [count.desc()]
        order += [column for column, _ in keys]
        result = await self.session.execute(stmt.order_by(*order))
        return [dict(row._mapping) for row in result]
