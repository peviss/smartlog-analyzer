"""Test configuration.

Unit tests need nothing external. Integration tests run against a real PostgreSQL
(the same engine as production, so JSONB, date_trunc and constraints are really
exercised). Point TEST_DATABASE_URL at a disposable database; it is migrated with
Alembic once per session and truncated before every test.
"""

import os

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://smartlog:smartlog@localhost:5432/smartlog_test"
)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["ENVIRONMENT"] = "test"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["LLM_PROVIDER"] = "mock"
os.environ["MOCK_LLM_LATENCY_MS"] = "0"

from collections.abc import AsyncIterator, Callable  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from asgi_lifespan import LifespanManager  # noqa: E402
from pydantic_ai.models import Model  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from api.core.config import get_settings  # noqa: E402
from api.llm.analyzer import LogAnalyzer  # noqa: E402
from api.main import create_app  # noqa: E402
from api.routers.deps import get_analyzer  # noqa: E402
from tests.fakes import valid_model  # noqa: E402


def _db_available() -> bool:
    try:
        engine = create_engine(TEST_DATABASE_URL)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def migrated_db():
    if not _db_available():
        pytest.skip(f"PostgreSQL not reachable at TEST_DATABASE_URL={TEST_DATABASE_URL}")
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield TEST_DATABASE_URL


@pytest.fixture
def clean_db(migrated_db):
    engine = create_engine(migrated_db)
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE analysis_logs, analyses, log_entries RESTART IDENTITY CASCADE"))
    yield engine
    engine.dispose()


ClientFactory = Callable[..., "AsyncIterator[httpx.AsyncClient]"]


@pytest.fixture
async def make_client(clean_db):
    """Build an API client with a chosen Pydantic AI model and timeout."""
    managers: list[LifespanManager] = []
    clients: list[httpx.AsyncClient] = []

    async def factory(
        model: Model | None = None, llm_timeout: float = 2.0, **settings_overrides
    ) -> httpx.AsyncClient:
        model = model or valid_model()
        app = create_app(llm_model=model)
        app.dependency_overrides[get_analyzer] = lambda: LogAnalyzer(model, timeout_seconds=llm_timeout)
        if settings_overrides:
            settings = get_settings().model_copy(update=settings_overrides)
            app.dependency_overrides[get_settings] = lambda: settings
        manager = LifespanManager(app)
        await manager.__aenter__()
        managers.append(manager)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=manager.app, raise_app_exceptions=False), base_url="http://test"
        )
        clients.append(client)
        return client

    yield factory

    for client in clients:
        await client.aclose()
    for manager in reversed(managers):
        await manager.__aexit__(None, None, None)


@pytest.fixture
async def client(make_client) -> httpx.AsyncClient:
    return await make_client()


def assert_error(response: httpx.Response, status: int, code: str) -> dict:
    """Every error must use the same envelope."""
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error", "request_id"}, body
    assert body["error"]["code"] == code, body
    assert body["error"]["message"]
    assert body["request_id"] == response.headers["x-request-id"]
    return body
