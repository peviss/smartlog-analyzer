import json
import uuid

import pytest
from pydantic_ai.exceptions import ModelHTTPError
from sqlalchemy import text

from tests.conftest import assert_error
from tests.fakes import FakeModel, Text, valid_model

pytestmark = pytest.mark.integration


async def seed(client, n_errors=3, n_info=2):
    logs = [
        {
            "service.name": "payments-api",
            "log.level": "ERROR",
            "message": f"Could not acquire DB connection after {i}ms",
        }
        for i in range(n_errors)
    ] + [
        {"service.name": "orders-api", "log.level": "INFO", "message": f"Order {i} created"}
        for i in range(n_info)
    ]
    response = await client.post("/api/logs/batch", json={"logs": logs})
    return response.json()["ids"]


async def test_analyze_by_ids_persists_and_can_be_fetched(make_client, clean_db):
    model = valid_model()
    client = await make_client(model)
    ids = await seed(client)

    response = await client.post("/api/logs/analyze", json={"log_ids": ids})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["log_count"] == 5
    assert body["pattern_count"] == 2
    assert body["stats"]["by_level"] == {"error": 3, "info": 2}
    assert body["summary"].startswith("payments-api")
    assert body["categories"][0]["severity"] == "critical"
    assert body["suggestions"][0]["priority"] == "high"
    assert body["provider"] == "fake" and body["error"] is None
    assert body["completed_at"] and body["duration_ms"] is not None

    fetched = await client.get(f"/api/analysis/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json() == body

    with clean_db.connect() as conn:
        linked = conn.execute(
            text("SELECT count(*) FROM analysis_logs WHERE analysis_id = :id"), {"id": body["id"]}
        ).scalar()
        raw = conn.execute(
            text("SELECT raw_response FROM analyses WHERE id = :id"), {"id": body["id"]}
        ).scalar()
    assert linked == 5
    assert json.loads(raw) == json.loads(model.text)


async def test_analyze_by_filters_selects_most_recent_matching(make_client):
    model = valid_model()
    client = await make_client(model)
    await seed(client, n_errors=4, n_info=3)

    response = await client.post(
        "/api/logs/analyze",
        json={"filters": {"service.name": ["payments-api"], "log.level": ["err"], "limit": 2}},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["log_count"] == 2
    assert body["request"] == {
        "filters": {"service.name": ["payments-api"], "log.level": ["error"], "limit": 2}
    }
    _, user_prompt = model.calls[0]
    assert "orders-api" not in user_prompt


async def test_analyze_with_mock_provider_end_to_end(make_client):
    from api.llm.mock import MockModel

    client = await make_client(MockModel(latency_ms=0))
    await seed(client)
    body = (await client.post("/api/logs/analyze", json={"filters": {}})).json()
    assert body["status"] == "completed"
    assert body["categories"][0]["name"] == "Database connectivity"


async def test_hallucinated_log_ids_are_removed(make_client):
    ids_holder = {}

    def echo_ids(user_prompt):
        data = json.loads(valid_model().text)
        data["categories"][0]["example_log_ids"] = [ids_holder["ids"][0], 999_999]
        return data

    client = await make_client(FakeModel(echo_ids))
    ids_holder["ids"] = await seed(client)
    body = (await client.post("/api/logs/analyze", json={"log_ids": ids_holder["ids"]})).json()
    assert body["categories"][0]["example_log_ids"] == [ids_holder["ids"][0]]


# ------------------------------------------------------------------ LLM failures are persisted


async def _failed_analysis(client, response, status, code):
    body = assert_error(response, status, code)
    analysis_id = body["error"]["analysis_id"]
    stored = (await client.get(f"/api/analysis/{analysis_id}")).json()
    assert stored["status"] == "failed"
    assert stored["error"]["code"] == code
    assert stored["summary"] is None and stored["categories"] is None
    assert stored["completed_at"] is not None
    return stored


async def test_unexpected_llm_format_returns_502_and_persists_failure(make_client, clean_db):
    client = await make_client(FakeModel(Text("I'm sorry, I can't help with that.")))
    ids = await seed(client)
    stored = await _failed_analysis(
        client, await client.post("/api/logs/analyze", json={"log_ids": ids}), 502, "LLM_INVALID_RESPONSE"
    )
    with clean_db.connect() as conn:
        raw = conn.execute(
            text("SELECT raw_response FROM analyses WHERE id = :id"), {"id": stored["id"]}
        ).scalar()
    assert "I'm sorry, I can't help with that." in raw, "raw output kept for debugging"


async def test_llm_schema_mismatch_returns_502(make_client):
    client = await make_client(FakeModel({"summary": "ok", "categories": "lots"}))
    ids = await seed(client)
    await _failed_analysis(
        client, await client.post("/api/logs/analyze", json={"log_ids": ids}), 502, "LLM_INVALID_RESPONSE"
    )


async def test_llm_timeout_returns_504_and_persists_failure(make_client):
    client = await make_client(FakeModel(delay=5), llm_timeout=0.2)
    ids = await seed(client)
    stored = await _failed_analysis(
        client, await client.post("/api/logs/analyze", json={"log_ids": ids}), 504, "LLM_TIMEOUT"
    )
    assert 200 <= stored["duration_ms"] < 2000


async def test_llm_provider_error_returns_502(make_client):
    client = await make_client(FakeModel(ModelHTTPError(529, "fake-model")))
    ids = await seed(client)
    await _failed_analysis(
        client, await client.post("/api/logs/analyze", json={"log_ids": ids}), 502, "LLM_PROVIDER_ERROR"
    )


# ------------------------------------------------------------------ input errors (LLM never called)


async def test_analyze_unknown_ids_returns_404_with_missing_list(make_client):
    model = valid_model()
    client = await make_client(model)
    ids = await seed(client)
    body = assert_error(
        await client.post("/api/logs/analyze", json={"log_ids": [ids[0], 98765, 98766]}),
        404,
        "LOGS_NOT_FOUND",
    )
    assert body["error"]["details"]["missing_ids"] == [98765, 98766]
    assert model.calls == []


async def test_analyze_filters_matching_nothing_returns_422(make_client):
    model = valid_model()
    client = await make_client(model)
    await seed(client)
    assert_error(
        await client.post("/api/logs/analyze", json={"filters": {"service.name": ["ghost-service"]}}),
        422,
        "NO_LOGS_MATCHED",
    )
    assert model.calls == []


@pytest.mark.parametrize(
    "payload",
    [{}, {"log_ids": [1], "filters": {}}, {"log_ids": []}, {"log_ids": ["a"]}, {"filters": {"limit": 9999}}],
)
async def test_analyze_invalid_requests(client, payload):
    assert_error(await client.post("/api/logs/analyze", json=payload), 422, "VALIDATION_ERROR")


async def test_get_unknown_analysis_returns_404(client):
    assert_error(await client.get(f"/api/analysis/{uuid.uuid4()}"), 404, "NOT_FOUND")


async def test_get_analysis_with_invalid_id_returns_422(client):
    body = assert_error(await client.get("/api/analysis/not-a-uuid"), 422, "VALIDATION_ERROR")
    assert body["error"]["details"][0]["field"] == "analysis_id"
