"""Initial schema: log_entries, analyses, analysis_logs.

Revision ID: 0001
Revises:
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "log_entries",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("service", sa.String(100), nullable=False),
        sa.Column("level", sa.String(10), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("trace_id", sa.String(64), nullable=True),
        sa.Column(
            "attributes",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "level IN ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL')",
            name="ck_log_entries_level",
        ),
    )
    op.create_index("ix_log_entries_timestamp", "log_entries", ["timestamp"])
    op.create_index("ix_log_entries_service_timestamp", "log_entries", ["service", "timestamp"])
    op.create_index("ix_log_entries_level_timestamp", "log_entries", ["level", "timestamp"])
    op.create_index(
        "ix_log_entries_trace_id",
        "log_entries",
        ["trace_id"],
        postgresql_where=sa.text("trace_id IS NOT NULL"),
    )

    op.create_table(
        "analyses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("request", postgresql.JSONB(), nullable=False),
        sa.Column("log_count", sa.Integer(), nullable=False),
        sa.Column("pattern_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stats", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("categories", postgresql.JSONB(), nullable=True),
        sa.Column("suggestions", postgresql.JSONB(), nullable=True),
        sa.Column("error_code", sa.String(50), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("raw_response", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.CheckConstraint("status IN ('pending', 'completed', 'failed')", name="ck_analyses_status"),
    )
    op.create_index("ix_analyses_created_at", "analyses", ["created_at"])

    op.create_table(
        "analysis_logs",
        sa.Column(
            "analysis_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("analyses.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "log_id",
            sa.BigInteger(),
            sa.ForeignKey("log_entries.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_index("ix_analysis_logs_log_id", "analysis_logs", ["log_id"])


def downgrade() -> None:
    op.drop_index("ix_analysis_logs_log_id", table_name="analysis_logs")
    op.drop_table("analysis_logs")
    op.drop_index("ix_analyses_created_at", table_name="analyses")
    op.drop_table("analyses")
    op.drop_index("ix_log_entries_trace_id", table_name="log_entries")
    op.drop_index("ix_log_entries_level_timestamp", table_name="log_entries")
    op.drop_index("ix_log_entries_service_timestamp", table_name="log_entries")
    op.drop_index("ix_log_entries_timestamp", table_name="log_entries")
    op.drop_table("log_entries")
