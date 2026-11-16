"""Ingestion reliability: idempotent retries and indexed text search.

- event_id (ECS event.id) with a unique index per service.name, so a retried log is not
  stored twice (INSERT ... ON CONFLICT DO NOTHING).
- pg_trgm GIN index on message, used by the `q` filter (ILIKE '%term%').

Existing rows keep any event.id they had inside `extra`; it is still returned, but it is
not backfilled into the new column, so those rows are not deduplicated against.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("log_entries", sa.Column("event_id", sa.String(128), nullable=True))
    op.create_index(
        "ux_log_entries_service_name_event_id",
        "log_entries",
        ["service_name", "event_id"],
        unique=True,
        postgresql_where=sa.text("event_id IS NOT NULL"),
    )
    # pg_trgm is a trusted extension (PostgreSQL 13+): the database owner can create it.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_index(
        "ix_log_entries_message_trgm",
        "log_entries",
        ["message"],
        postgresql_using="gin",
        postgresql_ops={"message": "gin_trgm_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_log_entries_message_trgm", table_name="log_entries")
    op.drop_index("ux_log_entries_service_name_event_id", table_name="log_entries")
    op.drop_column("log_entries", "event_id")
    # The pg_trgm extension is left installed: other objects may use it.
