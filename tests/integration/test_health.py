import pytest

import api.routers.health as health_module

pytestmark = pytest.mark.integration


async def test_health_ok(client):
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.1.0", "database": "ok", "llm_provider": "fake"}


async def test_health_reports_database_down_with_503(client, monkeypatch):
    def broken_sessionmaker():
        raise ConnectionRefusedError("db is down")

    monkeypatch.setattr(health_module, "get_sessionmaker", broken_sessionmaker)
    response = await client.get("/api/health")
    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["database"] == "unavailable"


async def test_unhandled_errors_return_generic_500(client, monkeypatch):
    """Internal details must never leak to the client."""
    from api.repositories import logs as log_repository

    async def explode(self, *args, **kwargs):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(log_repository.LogRepository, "search", explode)
    response = await client.get("/api/logs")
    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert "secret" not in response.text
