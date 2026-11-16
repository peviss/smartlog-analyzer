import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from api.database import tables  # noqa: F401
from api.database.session import Base

pytestmark = pytest.mark.integration


def test_migrations_match_orm_models(migrated_db):
    """If someone changes a model without writing a migration, this fails."""
    engine = create_engine(migrated_db)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()
    assert diff == []


def test_level_check_constraint_is_enforced_by_the_database(clean_db):
    """The normalized level is constrained; the original log.level is free text (ECS)."""
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError), clean_db.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO log_entries (timestamp, service_name, log_level, level, event_severity, message) "
                "VALUES (now(), 'a', 'LOUD', 'LOUD', 3, 'x')"
            )
        )


def test_0002_migrates_existing_rows_to_ecs_and_back(clean_db, migrated_db):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", migrated_db)
    command.downgrade(cfg, "0001")
    try:
        with clean_db.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO log_entries (timestamp, service, level, message, attributes) VALUES "
                    "(now(), 'payments', 'WARNING', 'slow', '{\"host\": \"p1\"}'), "
                    "(now(), 'payments', 'CRITICAL', 'down', '{}')"
                )
            )
        command.upgrade(cfg, "0002")
        with clean_db.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT service_name, log_level, level, event_severity, extra FROM log_entries ORDER BY id"
                )
            ).all()
        assert [tuple(r) for r in rows] == [
            ("payments", "WARNING", "warn", 4, {"host": "p1"}),
            ("payments", "CRITICAL", "critical", 2, {}),
        ]

        command.downgrade(cfg, "0001")
        with clean_db.connect() as conn:
            levels = conn.execute(text("SELECT level FROM log_entries ORDER BY id")).scalars().all()
        assert levels == ["WARNING", "CRITICAL"]
    finally:
        command.upgrade(cfg, "head")


def test_0003_adds_event_id_and_downgrades_cleanly(clean_db, migrated_db):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", migrated_db)
    try:
        command.downgrade(cfg, "0002")
        columns = {c["name"] for c in inspect(clean_db).get_columns("log_entries")}
        assert "event_id" not in columns
        command.upgrade(cfg, "0003")
        indexes = {i["name"] for i in inspect(clean_db).get_indexes("log_entries")}
        assert {"ux_log_entries_service_name_event_id", "ix_log_entries_message_trgm"} <= indexes
    finally:
        command.upgrade(cfg, "head")
