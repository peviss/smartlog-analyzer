"""ORM models: logs, analyses and the link between them."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.database.session import Base


class LogLevel(enum.StrEnum):
    """Normalized level derived from the original ECS `log.level` (see api.domain.ecs)."""

    CRITICAL = "critical"
    ERROR = "error"
    WARN = "warn"
    INFO = "info"
    DEBUG = "debug"


class AnalysisStatus(enum.StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


LEVEL_VALUES = ", ".join(f"'{lvl.value}'" for lvl in LogLevel)
STATUS_VALUES = ", ".join(f"'{s.value}'" for s in AnalysisStatus)


class LogEntry(Base):
    """One log event. Columns map 1:1 to ECS fields (see api.domain.ecs / api.schemas.to_ecs_document)."""

    __tablename__ = "log_entries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # @timestamp: when the event happened at the source
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    # log.level exactly as sent (ECS: original level) ...
    log_level: Mapped[str] = mapped_column(String(32), nullable=False)
    # ... plus derived values used for filtering, stats and analysis
    level: Mapped[str] = mapped_column(String(10), nullable=False)
    event_severity: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    log_logger: Mapped[str | None] = mapped_column(String(256))
    service_name: Mapped[str] = mapped_column(String(100), nullable=False)
    service_version: Mapped[str | None] = mapped_column(String(64))
    service_environment: Mapped[str | None] = mapped_column(String(64))
    host_name: Mapped[str | None] = mapped_column(String(255))
    trace_id: Mapped[str | None] = mapped_column(String(64))
    span_id: Mapped[str | None] = mapped_column(String(64))
    transaction_id: Mapped[str | None] = mapped_column(String(64))
    error_type: Mapped[str | None] = mapped_column(String(256))
    error_message: Mapped[str | None] = mapped_column(Text)
    error_stack_trace: Mapped[str | None] = mapped_column(Text)
    # event.id: the source's id for the event; unique per service so retries are not stored twice
    event_id: Mapped[str | None] = mapped_column(String(128))
    event_dataset: Mapped[str | None] = mapped_column(String(100))
    # event.original: raw event as received, only if the sender includes it
    event_original: Mapped[str | None] = mapped_column(Text)
    labels: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    tags: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    ecs_version: Mapped[str | None] = mapped_column(String(20))
    # Any other ECS or custom field, stored nested and returned as sent
    extra: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    # event.ingested: when SmartLog received it
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(f"level IN ({LEVEL_VALUES})", name="ck_log_entries_level"),
        CheckConstraint("event_severity BETWEEN 0 AND 7", name="ck_log_entries_event_severity"),
        Index("ix_log_entries_timestamp", "timestamp"),
        Index("ix_log_entries_service_name_timestamp", "service_name", "timestamp"),
        Index("ix_log_entries_level_timestamp", "level", "timestamp"),
        Index("ix_log_entries_trace_id", "trace_id", postgresql_where="trace_id IS NOT NULL"),
        Index(
            "ux_log_entries_service_name_event_id",
            "service_name",
            "event_id",
            unique=True,
            postgresql_where="event_id IS NOT NULL",
        ),
        # Trigram index so `q` (ILIKE '%term%') does not scan the whole table.
        Index(
            "ix_log_entries_message_trgm",
            "message",
            postgresql_using="gin",
            postgresql_ops={"message": "gin_trgm_ops"},
        ),
    )


class Analysis(Base):
    __tablename__ = "analyses"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=AnalysisStatus.PENDING)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # What was requested (log ids or filters) so the analysis can be reproduced.
    request: Mapped[dict] = mapped_column(JSONB, nullable=False)
    log_count: Mapped[int] = mapped_column(Integer, nullable=False)
    pattern_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Deterministic stats computed before calling the LLM (counts per level/service, time window).
    stats: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    # LLM result
    summary: Mapped[str | None] = mapped_column(Text)
    categories: Mapped[list | None] = mapped_column(JSONB)
    suggestions: Mapped[list | None] = mapped_column(JSONB)
    # Failure info
    error_code: Mapped[str | None] = mapped_column(String(50))
    error_message: Mapped[str | None] = mapped_column(Text)
    raw_response: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    logs: Mapped[list["AnalysisLog"]] = relationship(back_populates="analysis", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint(f"status IN ({STATUS_VALUES})", name="ck_analyses_status"),
        Index("ix_analyses_created_at", "created_at"),
    )


class AnalysisLog(Base):
    """Which logs were included in each analysis (many-to-many)."""

    __tablename__ = "analysis_logs"

    analysis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analyses.id", ondelete="CASCADE"), primary_key=True
    )
    log_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("log_entries.id", ondelete="CASCADE"), primary_key=True
    )

    analysis: Mapped[Analysis] = relationship(back_populates="logs")

    __table_args__ = (Index("ix_analysis_logs_log_id", "log_id"),)
