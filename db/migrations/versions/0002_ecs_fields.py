"""Adopt Elastic Common Schema (ECS) fields for logs.

- service -> service_name (service.name), attributes -> extra
- level becomes the normalized level (critical/error/warn/info/debug);
  the original spelling moves to log_level (log.level) and event_severity is derived
- new optional ECS columns: log.logger, service.version/environment, host.name,
  span.id, transaction.id, error.*, event.dataset, event.original, labels, tags, ecs.version

Existing rows are migrated in place. Downgrade maps levels back to the old
uppercase vocabulary and drops the new columns (their data is lost).

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_NULLABLE_COLUMNS = [
    ("log_logger", sa.String(256)),
    ("service_version", sa.String(64)),
    ("service_environment", sa.String(64)),
    ("host_name", sa.String(255)),
    ("span_id", sa.String(64)),
    ("transaction_id", sa.String(64)),
    ("error_type", sa.String(256)),
    ("error_message", sa.Text()),
    ("error_stack_trace", sa.Text()),
    ("event_dataset", sa.String(100)),
    ("event_original", sa.Text()),
    ("ecs_version", sa.String(20)),
]


def upgrade() -> None:
    op.alter_column("log_entries", "service", new_column_name="service_name")
    op.alter_column("log_entries", "attributes", new_column_name="extra")
    op.execute("ALTER INDEX ix_log_entries_service_timestamp RENAME TO ix_log_entries_service_name_timestamp")

    # Original level is kept as it was stored; then level becomes the normalized value.
    op.add_column("log_entries", sa.Column("log_level", sa.String(32), nullable=True))
    op.add_column("log_entries", sa.Column("event_severity", sa.SmallInteger(), nullable=True))
    op.drop_constraint("ck_log_entries_level", "log_entries", type_="check")
    op.execute(
        """
        UPDATE log_entries SET
            log_level = level,
            level = CASE level WHEN 'WARNING' THEN 'warn' ELSE lower(level) END,
            event_severity = CASE level
                WHEN 'CRITICAL' THEN 2 WHEN 'ERROR' THEN 3 WHEN 'WARNING' THEN 4
                WHEN 'INFO' THEN 6 ELSE 7 END
        """
    )
    op.alter_column("log_entries", "log_level", nullable=False)
    op.alter_column("log_entries", "event_severity", nullable=False)
    op.create_check_constraint(
        "ck_log_entries_level", "log_entries", "level IN ('critical', 'error', 'warn', 'info', 'debug')"
    )
    op.create_check_constraint(
        "ck_log_entries_event_severity", "log_entries", "event_severity BETWEEN 0 AND 7"
    )

    for name, type_ in NEW_NULLABLE_COLUMNS:
        op.add_column("log_entries", sa.Column(name, type_, nullable=True))
    op.add_column(
        "log_entries",
        sa.Column("labels", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column(
        "log_entries",
        sa.Column("tags", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("log_entries", "tags")
    op.drop_column("log_entries", "labels")
    for name, _ in reversed(NEW_NULLABLE_COLUMNS):
        op.drop_column("log_entries", name)

    op.drop_constraint("ck_log_entries_event_severity", "log_entries", type_="check")
    op.drop_constraint("ck_log_entries_level", "log_entries", type_="check")
    op.execute(
        """
        UPDATE log_entries SET level = CASE level
            WHEN 'warn' THEN 'WARNING' ELSE upper(level) END
        """
    )
    op.create_check_constraint(
        "ck_log_entries_level", "log_entries", "level IN ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL')"
    )
    op.drop_column("log_entries", "event_severity")
    op.drop_column("log_entries", "log_level")

    op.execute("ALTER INDEX ix_log_entries_service_name_timestamp RENAME TO ix_log_entries_service_timestamp")
    op.alter_column("log_entries", "extra", new_column_name="attributes")
    op.alter_column("log_entries", "service_name", new_column_name="service")
