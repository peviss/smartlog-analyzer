import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from api.domain.ecs import ECS_VERSION
from tests.conftest import assert_error

pytestmark = pytest.mark.integration

NOW = datetime.now(UTC).replace(microsecond=0)


def log(**overrides):
    """A minimal ECS document (dotted keys, as ecs-logging libraries emit)."""
    return {
        "service.name": "payments-api",
        "log.level": "ERROR",
        "message": "Payment failed for order 1",
        **overrides,
    }


async def seed(client, logs):
    response = await client.post("/api/logs/batch", json={"logs": logs})
    assert response.status_code == 201, response.text
    return response.json()["ids"]


# ------------------------------------------------------------------ ingest


async def test_ingest_returns_nested_ecs_document(client):
    response = await client.post(
        "/api/logs",
        json=log(
            **{
                "@timestamp": "2026-11-16T10:00:00-03:00",
                "trace.id": "abc-123",
                "service.version": "1.4.2",
                "labels": {"team": "payments"},
            }
        ),
    )
    assert response.status_code == 201
    body = response.json()
    assert body["id"] > 0
    assert body["@timestamp"] == "2026-11-16T13:00:00Z"
    assert body["log"] == {"level": "ERROR"}
    assert body["service"] == {"name": "payments-api", "version": "1.4.2"}
    assert body["trace"] == {"id": "abc-123"}
    assert body["labels"] == {"team": "payments"}
    assert body["event"]["severity"] == 3
    assert body["event"]["ingested"]
    assert body["ecs"] == {"version": ECS_VERSION}
    assert "host" not in body and "error" not in body, "empty ECS objects are omitted"


async def test_ingest_nested_style_and_custom_fields_round_trip(client):
    payload = {
        "@timestamp": "2026-11-16T13:00:00Z",
        "log": {"level": "Warning", "logger": "Notifications.Worker", "origin": {"file": {"line": 42}}},
        "message": "Memory at 91%",
        "service": {"name": "notification-worker", "environment": "prod"},
        "host": {"name": "worker-2"},
        "error": {"type": "OutOfMemoryWarning", "stack_trace": "at Worker.Run()\nat Main()"},
        "http": {"response": {"status_code": 503}},
        "tags": ["memory"],
        "ecs": {"version": "8.11.0"},
    }
    body = (await client.post("/api/logs", json=payload)).json()
    assert body["log"] == {
        "level": "Warning",
        "logger": "Notifications.Worker",
        "origin": {"file": {"line": 42}},
    }
    assert body["service"] == {"name": "notification-worker", "environment": "prod"}
    assert body["error"] == {"type": "OutOfMemoryWarning", "stack_trace": "at Worker.Run()\nat Main()"}
    assert body["http"] == {"response": {"status_code": 503}}
    assert body["tags"] == ["memory"]
    assert body["ecs"] == {"version": "8.11.0"}, "sender's ECS version is kept"
    assert body["event"]["severity"] == 4

    listed = (await client.get("/api/logs")).json()["items"][0]
    assert listed == body, "GET returns exactly the stored document"


async def test_ingest_without_timestamp_uses_now(client):
    response = await client.post("/api/logs", json=log())
    ts = datetime.fromisoformat(response.json()["@timestamp"])
    assert abs((datetime.now(UTC) - ts).total_seconds()) < 10


async def test_ingest_keeps_original_level_and_normalizes_service(client):
    response = await client.post("/api/logs", json=log(**{"log.level": "WARN", "service.name": "Orders-API"}))
    assert response.status_code == 201
    assert response.json()["log"]["level"] == "WARN"
    assert response.json()["service"]["name"] == "orders-api"


async def test_client_cannot_set_server_managed_fields(client):
    body = (
        await client.post(
            "/api/logs", json=log(**{"event.severity": 0, "event.ingested": "2000-01-01T00:00:00Z"})
        )
    ).json()
    assert body["event"]["severity"] == 3
    assert not body["event"]["ingested"].startswith("2000")


async def test_ingest_unicode_message_round_trips(client):
    message = "Pago rechazado: tarjeta inválida ✗ — 日本語"
    response = await client.post("/api/logs", json=log(message=message))
    assert response.json()["message"] == message


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        (log(**{"log.level": "   "}), "log.level"),
        (log(**{"service.name": "bad service"}), "service.name"),
        (log(message="   "), "message"),
        ({"service.name": "a", "log.level": "info"}, "message"),
        ({"message": "x", "log.level": "info"}, "service.name"),
        (log(**{"@timestamp": "not-a-date"}), "@timestamp"),
        (log(**{"@timestamp": (NOW + timedelta(days=1)).isoformat()}), "@timestamp"),
        (log(labels="nope"), "labels"),
        (log(**{"trace.id": "has spaces"}), "trace.id"),
    ],
)
async def test_ingest_validation_errors_use_ecs_field_names(client, payload, field):
    body = assert_error(await client.post("/api/logs", json=payload), 422, "VALIDATION_ERROR")
    assert any(d["field"] == field for d in body["error"]["details"]), body


async def test_ingest_conflicting_values_are_rejected(client):
    body = assert_error(
        await client.post("/api/logs", json=log(log={"level": "info"})), 422, "VALIDATION_ERROR"
    )
    assert "given twice" in body["error"]["details"][0]["message"]


async def test_ingest_malformed_json_body(client):
    response = await client.post(
        "/api/logs", content=b'{"service.name": "a", ', headers={"content-type": "application/json"}
    )
    assert_error(response, 422, "VALIDATION_ERROR")


async def test_ingest_body_must_be_an_object(client):
    assert_error(await client.post("/api/logs", json=[log()]), 422, "VALIDATION_ERROR")


async def test_batch_ingest(client):
    response = await client.post("/api/logs/batch", json={"logs": [log(), log(**{"log.level": "info"})]})
    assert response.status_code == 201
    assert response.json()["ingested"] == 2
    assert len(response.json()["ids"]) == 2


async def test_batch_stores_valid_logs_and_reports_invalid_ones(client, clean_db):
    response = await client.post(
        "/api/logs/batch",
        json={"logs": [log(), log(message="   "), "not an object", log(**{"log.level": "info"})]},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["ingested"], body["failed"], body["duplicates"]) == (2, 2, 0)
    assert [e["index"] for e in body["errors"]] == [1, 2]
    assert body["errors"][0]["errors"][0]["field"] == "logs.1.message"
    with clean_db.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM log_entries")).scalar() == 2


async def test_batch_with_no_valid_log_is_rejected(client, clean_db):
    body = assert_error(
        await client.post(
            "/api/logs/batch", json={"logs": [log(message=" "), log(**{"service.name": "x y"})]}
        ),
        422,
        "VALIDATION_ERROR",
    )
    assert [d["field"] for d in body["error"]["details"]] == ["logs.0.message", "logs.1.service.name"]
    with clean_db.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM log_entries")).scalar() == 0


async def test_batch_empty_is_rejected(client):
    assert_error(await client.post("/api/logs/batch", json={"logs": []}), 422, "VALIDATION_ERROR")


# ------------------------------------------------------------------ list


async def test_list_filters_and_ordering(client):
    await seed(
        client,
        [
            log(**{"service.name": "payments-api", "log.level": "ERROR", "message": "db down",
                   "@timestamp": (NOW - timedelta(hours=3)).isoformat(), "host.name": "pay-1"}),
            log(**{"service.name": "payments-api", "log.level": "INFO", "message": "ok",
                   "@timestamp": (NOW - timedelta(hours=2)).isoformat(), "service.environment": "prod"}),
            log(**{"service.name": "orders-api", "log.level": "WARN", "message": "slow",
                   "@timestamp": (NOW - timedelta(hours=1)).isoformat(), "event.dataset": "orders.app"}),
            log(**{"service.name": "orders-api", "log.level": "FATAL", "message": "db down hard",
                   "@timestamp": NOW.isoformat(), "trace.id": "t-1"}),
        ],
    )  # fmt: skip

    body = (await client.get("/api/logs")).json()
    assert body["page"]["total"] == 4
    assert body["items"][0]["message"] == "db down hard", "newest first"

    r = await client.get("/api/logs", params={"service.name": "payments-api"})
    assert {i["service"]["name"] for i in r.json()["items"]} == {"payments-api"}

    # log.level filters on the normalized level: "warning" matches WARN, "critical" matches FATAL
    r = await client.get("/api/logs", params=[("log.level", "warning"), ("log.level", "critical")])
    assert {i["log"]["level"] for i in r.json()["items"]} == {"WARN", "FATAL"}

    frm = (NOW - timedelta(hours=2, minutes=30)).isoformat()
    to = (NOW - timedelta(minutes=30)).isoformat()
    r = await client.get("/api/logs", params={"from": frm, "to": to})
    assert {i["message"] for i in r.json()["items"]} == {"ok", "slow"}

    r = await client.get("/api/logs", params={"q": "DB DOWN"})
    assert r.json()["page"]["total"] == 2, "text search is case-insensitive"

    for params, expected in [
        ({"trace.id": "t-1"}, "db down hard"),
        ({"host.name": "pay-1"}, "db down"),
        ({"service.environment": "prod"}, "ok"),
        ({"event.dataset": "orders.app"}, "slow"),
    ]:
        items = (await client.get("/api/logs", params=params)).json()["items"]
        assert [i["message"] for i in items] == [expected], params


async def test_text_search_treats_wildcards_literally(client):
    await seed(client, [log(message="CPU at 100% now"), log(message="CPU at 1000 now")])
    r = await client.get("/api/logs", params={"q": "100%"})
    assert [i["message"] for i in r.json()["items"]] == ["CPU at 100% now"]
    r = await client.get("/api/logs", params={"q": "_"})
    assert r.json()["page"]["total"] == 0


async def test_pagination(client):
    await seed(client, [log(message=f"m{i}") for i in range(5)])
    r = (await client.get("/api/logs", params={"limit": 2, "offset": 0})).json()
    assert len(r["items"]) == 2 and r["page"] == {"total": 5, "limit": 2, "offset": 0, "has_more": True}
    last = (await client.get("/api/logs", params={"limit": 2, "offset": 4})).json()
    assert len(last["items"]) == 1 and last["page"]["has_more"] is False
    beyond = (await client.get("/api/logs", params={"limit": 2, "offset": 50})).json()
    assert beyond["items"] == [] and beyond["page"]["total"] == 5


async def test_list_empty_database(client):
    body = (await client.get("/api/logs")).json()
    assert body["items"] == [] and body["page"]["total"] == 0


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 501},
        {"offset": -1},
        {"log.level": "LOUD"},
        {"from": "2026-11-16T10:00:00Z", "to": "2026-11-16T09:00:00Z"},
        {"from": "garbage"},
        {"service": "old-param-name"},
        {"unknown": "x"},
    ],
)
async def test_list_invalid_query(client, params):
    assert_error(await client.get("/api/logs", params=params), 422, "VALIDATION_ERROR")


# ------------------------------------------------------------------ stats


async def test_stats_by_service_and_normalized_level(client):
    await seed(
        client,
        [
            log(**{"service.name": "a", "log.level": "ERROR"}),
            log(**{"service.name": "a", "log.level": "err"}),  # same normalized level
            log(**{"service.name": "a", "log.level": "INFO"}),
            log(**{"service.name": "b", "log.level": "error"}),
        ],
    )
    body = (await client.get("/api/logs/stats")).json()
    assert body["group_by"] == ["service.name", "log.level"]
    assert body["total"] == 4
    assert body["buckets"][0] | {"first_seen": None, "last_seen": None} == {
        "service.name": "a",
        "log.level": "error",
        "count": 2,
        "first_seen": None,
        "last_seen": None,
    }
    assert "period" not in body["buckets"][0]


async def test_stats_by_hour_with_filters(client):
    base = NOW.replace(minute=0, second=0) - timedelta(hours=5)
    await seed(
        client,
        [
            log(**{"log.level": "ERROR", "@timestamp": (base + timedelta(minutes=5)).isoformat()}),
            log(**{"log.level": "ERROR", "@timestamp": (base + timedelta(minutes=50)).isoformat()}),
            log(**{"log.level": "ERROR", "@timestamp": (base + timedelta(hours=1, minutes=1)).isoformat()}),
            log(**{"log.level": "INFO", "@timestamp": (base + timedelta(minutes=10)).isoformat()}),
        ],
    )
    body = (await client.get("/api/logs/stats", params={"group_by": "hour", "log.level": "error"})).json()
    assert body["total"] == 3
    counts = {datetime.fromisoformat(b["period"]): b["count"] for b in body["buckets"]}
    assert counts == {base: 2, base + timedelta(hours=1): 1}
    assert list(counts) == sorted(counts, reverse=True), "most recent bucket first"


async def test_stats_by_environment_and_host(client):
    await seed(
        client,
        [
            log(**{"service.environment": "prod", "host.name": "h1"}),
            log(**{"service.environment": "prod", "host.name": "h1"}),
            log(**{"service.environment": "staging", "host.name": "h2"}),
        ],
    )
    params = [("group_by", "service.environment"), ("group_by", "host.name")]
    buckets = (await client.get("/api/logs/stats", params=params)).json()["buckets"]
    assert [(b["service.environment"], b["host.name"], b["count"]) for b in buckets] == [
        ("prod", "h1", 2),
        ("staging", "h2", 1),
    ]


async def test_stats_hour_wins_over_day(client):
    await seed(client, [log()])
    body = (await client.get("/api/logs/stats", params=[("group_by", "day"), ("group_by", "hour")])).json()
    assert body["group_by"] == ["hour"]


async def test_stats_empty(client):
    body = (await client.get("/api/logs/stats")).json()
    assert body == {"group_by": ["service.name", "log.level"], "total": 0, "buckets": []}


@pytest.mark.parametrize("group_by", ["week", "service", "level"])
async def test_stats_invalid_group_by(client, group_by):
    assert_error(await client.get("/api/logs/stats", params={"group_by": group_by}), 422, "VALIDATION_ERROR")


# ------------------------------------------------------------------ generic errors


async def test_unknown_route_uses_error_envelope(client):
    assert_error(await client.get("/api/nope"), 404, "NOT_FOUND")


async def test_wrong_method_uses_error_envelope(client):
    assert_error(await client.delete("/api/logs"), 405, "METHOD_NOT_ALLOWED")


async def test_request_id_is_propagated(client):
    response = await client.get("/api/logs", headers={"X-Request-ID": "my-req-1"})
    assert response.headers["x-request-id"] == "my-req-1"


# ------------------------------------------------------------------ nothing lost, nothing twice


async def test_unknown_level_is_stored_as_info_and_tagged(client):
    response = await client.post(
        "/api/logs", json=log(**{"log.level": "LOUD", "@timestamp": NOW.isoformat()})
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["log"]["level"] == "LOUD"
    assert body["event"]["severity"] == 6
    assert body["tags"] == ["_level_unrecognized"]
    listed = (await client.get("/api/logs", params={"log.level": "info"})).json()
    assert listed["page"]["total"] == 1


async def test_missing_timestamp_is_tagged(client):
    assert (await client.post("/api/logs", json=log())).json()["tags"] == ["_timestamp_missing"]


async def test_retry_with_same_event_id_returns_the_stored_log(client, clean_db):
    first = await client.post("/api/logs", json=log(event={"id": "evt-1"}))
    retry = await client.post("/api/logs", json=log(event={"id": "evt-1"}, message="changed"))
    assert (first.status_code, retry.status_code) == (201, 200)
    assert retry.json() == first.json()
    assert first.json()["event"]["id"] == "evt-1"
    with clean_db.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM log_entries")).scalar() == 1


async def test_event_id_is_unique_per_service(client):
    a = await client.post("/api/logs", json=log(event={"id": "evt-1"}))
    b = await client.post("/api/logs", json=log(event={"id": "evt-1"}, **{"service.name": "orders"}))
    assert (a.status_code, b.status_code) == (201, 201)


async def test_batch_skips_retried_and_repeated_event_ids(client, clean_db):
    await client.post("/api/logs", json=log(event={"id": "evt-1"}))
    response = await client.post(
        "/api/logs/batch",
        json={
            "logs": [
                log(event={"id": "evt-1"}),  # already stored
                log(event={"id": "evt-2"}),
                log(event={"id": "evt-2"}),  # repeated inside the batch
                log(),  # no event.id: never deduplicated
                log(),
            ]
        },
    )
    body = response.json()
    assert (body["ingested"], body["duplicates"], body["failed"]) == (3, 2, 0)
    assert body["ids"] == sorted(body["ids"])
    with clean_db.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM log_entries")).scalar() == 4


async def test_original_document_is_kept_when_enabled(make_client):
    client = await make_client(ingest_keep_original=True)
    sent = log(**{"@timestamp": NOW.isoformat(), "custom": {"a": 1}})
    body = (await client.post("/api/logs", json=sent)).json()
    assert json.loads(body["event"]["original"]) == sent
    batch = await client.post("/api/logs/batch", json={"logs": [sent]})
    stored = (await client.get("/api/logs", params={"q": "Payment"})).json()["items"]
    assert batch.status_code == 201 and all("original" in item["event"] for item in stored)


async def test_original_document_is_not_kept_by_default(client):
    body = (await client.post("/api/logs", json=log())).json()
    assert "original" not in body["event"]


async def test_text_search_can_use_the_trigram_index(clean_db):
    with clean_db.begin() as conn:
        conn.execute(text("SET LOCAL enable_seqscan = off"))
        plan = "\n".join(
            conn.execute(
                text("EXPLAIN SELECT id FROM log_entries WHERE message ILIKE '%connection refused%'")
            ).scalars()
        )
    assert "ix_log_entries_message_trgm" in plan, plan


# ------------------------------------------------------------------ get one


async def test_get_log_by_id_returns_the_ecs_document(client):
    created = (await client.post("/api/logs", json=log())).json()
    response = await client.get(f"/api/logs/{created['id']}")
    assert response.status_code == 200
    assert response.json() == created


async def test_get_missing_log_is_404(client):
    assert_error(await client.get("/api/logs/999999"), 404, "NOT_FOUND")


async def test_get_log_rejects_invalid_id(client):
    assert_error(await client.get("/api/logs/0"), 422, "VALIDATION_ERROR")


# ------------------------------------------------------------------ raw import


def _import(client, body: str, content_type: str, **params):
    return client.post(
        "/api/logs/import", content=body.encode(), headers={"content-type": content_type}, params=params
    )


async def test_import_plain_text_with_defaults_and_stack_traces(client):
    text = (
        f"{NOW.isoformat()} ERROR Payment failed\n"
        "\tat com.example.Pay.charge(Pay.java:42)\n"
        "\n"
        "something without a level\n"
    )
    response = await _import(client, text, "text/plain", **{"service.name": "payments-api"})
    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["format"], body["records"], body["ingested"], body["failed"]) == ("text", 2, 2, 0)

    logs = (await client.get("/api/logs")).json()["items"]
    by_message = {log["message"]: log for log in logs}
    error = by_message["Payment failed"]
    assert error["error"]["stack_trace"] == "\tat com.example.Pay.charge(Pay.java:42)"
    assert error["event"]["original"].startswith(f"{NOW.isoformat()} ERROR Payment failed\n\tat")
    plain = by_message["something without a level"]
    assert plain["log"]["level"] == "info"
    assert set(plain["tags"]) == {"_level_missing", "_timestamp_missing"}


async def test_import_reports_invalid_records_with_their_line(client):
    future = (NOW + timedelta(days=2)).isoformat()
    text = f"{NOW.isoformat()} ERROR ok\n\n{future} ERROR from the future\n"
    body = (await _import(client, text, "text/plain", **{"service.name": "payments-api"})).json()
    assert (body["ingested"], body["failed"]) == (1, 1)
    assert body["errors"][0]["line"] == 3
    assert body["errors"][0]["errors"][0]["field"] == "@timestamp"


async def test_import_fixes_invalid_service_names_instead_of_rejecting(client):
    body = (await _import(client, "<14>1 - - Bad%Service - - - hello\n", "text/plain")).json()
    assert (body["ingested"], body["failed"]) == (1, 0)
    log = (await client.get("/api/logs")).json()["items"][0]
    assert (log["service"]["name"], log["labels"]) == (
        "bad-service",
        {"original_service_name": "Bad%Service"},
    )


async def test_import_csv(client):
    csv_text = (
        "timestamp;level;service;message;labels.team\n"
        f"{NOW.isoformat()};ERROR;payments-api;Card declined;core\n"
        f"{NOW.isoformat()};WARN;orders;Slow;\n"
    )
    body = (await _import(client, csv_text, "text/csv")).json()
    assert (body["format"], body["ingested"]) == ("csv", 2)
    logs = (await client.get("/api/logs", params={"service.name": "payments-api"})).json()["items"]
    assert logs[0]["labels"] == {"team": "core"}
    assert logs[0]["log"]["level"] == "ERROR"


async def test_import_json_and_ndjson_map_common_field_names(client):
    array = '[{"msg": "from array", "level": "error", "service": "payments-api"}]'
    ndjson = '{"msg": "line 1", "level": 30, "service": "orders"}\nnot json\n'
    first = (await _import(client, array, "application/json")).json()
    second = (await _import(client, ndjson, "application/x-ndjson")).json()
    assert (first["ingested"], second["ingested"], second["failed"]) == (1, 1, 1)
    assert second["errors"][0] == {
        "index": 1,
        "line": 2,
        "errors": [
            {
                "field": None,
                "location": "body",
                "message": "invalid JSON: Expecting value",
                "type": "parse_error",
            }
        ],
    }
    levels = {log["message"]: log["log"]["level"] for log in (await client.get("/api/logs")).json()["items"]}
    assert levels == {"from array": "error", "line 1": "info"}


async def test_import_format_param_overrides_content_type(client):
    body = (
        await _import(
            client, "message,level\nhello,info\n", "text/plain", format="csv", **{"service.name": "a"}
        )
    ).json()
    assert (body["format"], body["ingested"]) == ("csv", 1)


async def test_import_is_idempotent_with_event_ids(client):
    ndjson = '{"msg": "x", "level": "info", "service": "a", "event.id": "e-1"}\n'
    await _import(client, ndjson, "application/x-ndjson")
    body = (await _import(client, ndjson, "application/x-ndjson")).json()
    assert (body["ingested"], body["duplicates"]) == (0, 1)


async def test_import_with_no_valid_record_is_422_with_lines(client, clean_db):
    body = assert_error(await _import(client, "first\nsecond\n", "text/plain"), 422, "VALIDATION_ERROR")
    assert [d["line"] for d in body["error"]["details"]] == [1, 2]
    assert {d["field"] for d in body["error"]["details"]} == {"service.name"}
    with clean_db.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM log_entries")).scalar() == 0


@pytest.mark.parametrize(
    ("body", "content_type", "message"),
    [
        (b"", "text/plain", "No log records"),
        (b"\xff\xfe broken", "text/plain", "UTF-8"),
        (b"\n\n  \n", "text/plain", "No log records"),
    ],
)
async def test_import_unreadable_input(client, body, content_type, message):
    response = await client.post("/api/logs/import", content=body, headers={"content-type": content_type})
    assert message in assert_error(response, 422, "INVALID_IMPORT")["error"]["message"]


async def test_import_too_many_records(client, monkeypatch):
    from api.routers import logs as logs_router

    monkeypatch.setattr(logs_router, "MAX_IMPORT_RECORDS", 2)
    response = await _import(client, "a\nb\nc\n", "text/plain", **{"service.name": "a"})
    assert "limit is 2" in assert_error(response, 422, "INVALID_IMPORT")["error"]["message"]


async def test_import_too_large(client, monkeypatch):
    from api.routers import logs as logs_router

    monkeypatch.setattr(logs_router, "MAX_IMPORT_BYTES", 10)
    assert_error(await _import(client, "x" * 11, "text/plain"), 413, "PAYLOAD_TOO_LARGE")


async def test_import_accepts_a_utf8_bom(client):
    body = "﻿message,level,service\nhola,info,a\n".encode()
    response = await client.post("/api/logs/import", content=body, headers={"content-type": "text/csv"})
    assert response.json()["ingested"] == 1


async def test_import_non_ecs_schemas_are_parsed_into_ecs(client):
    ndjson = "\n".join(
        [
            '{"timeUnixNano":"1763287200000000000","severityText":"ERROR","body":{"stringValue":"otel"},'
            '"resource":{"attributes":[{"key":"service.name","value":{"stringValue":"checkout"}}]}}',
            '{"version":"1.1","host":"web-1","short_message":"gelf","level":4,"_service":"storage"}',
            '{"log":"2026-11-16T10:00:00Z ERROR from docker\\n","stream":"stderr","time":"2026-11-16T10:00:00Z"}',
            '{"Message":"camel","logLevel":"WARN","serviceName":"Payments API","traceId":"t-1"}',
        ]
    )
    body = (await _import(client, ndjson, "application/x-ndjson", **{"service.name": "fallback"})).json()
    assert (body["ingested"], body["failed"]) == (4, 0), body

    logs = {log["message"]: log for log in (await client.get("/api/logs")).json()["items"]}
    assert (logs["otel"]["service"]["name"], logs["otel"]["event"]["severity"]) == ("checkout", 3)
    assert (logs["gelf"]["service"]["name"], logs["gelf"]["host"]["name"], logs["gelf"]["log"]["level"]) == (
        "storage",
        "web-1",
        "warning",
    )
    assert (logs["from docker"]["service"]["name"], logs["from docker"]["labels"]) == (
        "fallback",
        {"stream": "stderr"},
    )
    camel = logs["camel"]
    assert (camel["service"]["name"], camel["trace"]["id"]) == ("payments-api", "t-1")
    assert camel["labels"] == {"original_service_name": "Payments API"}
