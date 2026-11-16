import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from api.database.tables import LogEntry
from api.domain.ecs import ECS_VERSION
from api.schemas.analysis import AnalyzeRequest
from api.schemas.logs import LogBatchCreate, LogCreate, LogFilters, to_ecs_document, to_log_read

BASE = {"service.name": "payments-api", "log.level": "ERROR", "message": "boom"}


def make(**overrides):
    return LogCreate.model_validate({**BASE, **overrides})


def errors_for(payload) -> list[str]:
    with pytest.raises(ValidationError) as exc_info:
        LogCreate.model_validate(payload)
    return [".".join(map(str, e["loc"])) for e in exc_info.value.errors()]


# ------------------------------------------------------------------ ECS input shapes


def test_accepts_dotted_keys_like_ecs_logging_libraries():
    log = LogCreate.model_validate(
        {
            "@timestamp": "2026-11-16T10:00:00Z",
            "log.level": "INFO",
            "message": "Tomcat started",
            "ecs.version": "1.2.0",
            "service.name": "spring-petclinic",
            "log.logger": "org.springframework.boot.Tomcat",
            "trace.id": "2869b25b5469590610fea49ac04af7da",
            "transaction.id": "28b7fb8d5aba51f1",
        }
    )
    assert log.service_name == "spring-petclinic"
    assert log.log_logger == "org.springframework.boot.Tomcat"
    assert log.trace_id == "2869b25b5469590610fea49ac04af7da"
    assert log.ecs_version == "1.2.0"
    assert log.extra == {}


def test_accepts_nested_objects():
    log = LogCreate.model_validate(
        {
            "log": {"level": "warn", "logger": "x"},
            "message": "slow",
            "service": {"name": "orders", "version": "2.0", "environment": "prod"},
            "host": {"name": "orders-1"},
            "error": {"type": "TimeoutError", "message": "boom", "stack_trace": "line 1\nline 2"},
            "event": {"dataset": "orders.app", "original": '{"raw": true}'},
        }
    )
    assert (log.service_version, log.service_environment, log.host_name) == ("2.0", "prod", "orders-1")
    assert (log.error_type, log.error_stack_trace) == ("TimeoutError", "line 1\nline 2")
    assert (log.event_dataset, log.event_original) == ("orders.app", '{"raw": true}')


def test_unknown_ecs_and_custom_fields_are_kept_nested():
    log = make(
        **{"http.response.status_code": 503, "log.origin": {"file.line": 42}, "myapp": {"cart_id": "c1"}}
    )
    assert log.extra == {
        "http": {"response": {"status_code": 503}},
        "log": {"origin": {"file": {"line": 42}}},
        "myapp": {"cart_id": "c1"},
    }


def test_server_managed_fields_are_ignored():
    log = make(**{"event.severity": 99, "event.ingested": "2000-01-01T00:00:00Z"})
    assert log.extra == {}
    assert log.to_row()["event_severity"] == 3


def test_conflicting_dotted_and_nested_values_are_rejected():
    with pytest.raises(ValidationError, match="given twice"):
        make(log={"level": "info"})


# ------------------------------------------------------------------ log.level


@pytest.mark.parametrize(
    ("given", "normalized", "severity"),
    [("ERROR", "error", 3), ("Warning", "warn", 4), ("Information", "info", 6), ("FATAL", "critical", 2)],
)
def test_original_level_is_kept_and_normalized_values_derived(given, normalized, severity):
    log = make(**{"log.level": given})
    assert log.log_level == given
    row = log.to_row()
    assert (row["log_level"], row["level"], row["event_severity"]) == (given, normalized, severity)


def test_level_is_trimmed():
    assert make(**{"log.level": "  warn "}).log_level == "warn"


# ------------------------------------------------------------------ other rules


def test_service_name_is_normalised():
    assert make(**{"service.name": "  Payments-API "}).service_name == "payments-api"


def test_naive_timestamp_is_treated_as_utc():
    assert make(**{"@timestamp": "2026-11-16T10:00:00"}).timestamp == datetime(2026, 11, 16, 10, tzinfo=UTC)


def test_aware_timestamp_is_converted_to_utc():
    assert make(**{"@timestamp": "2026-11-16T10:00:00-03:00"}).timestamp == datetime(
        2026, 11, 16, 13, tzinfo=UTC
    )


def test_missing_timestamp_defaults_to_now_in_row():
    row = make().to_row()
    assert abs((datetime.now(UTC) - row["timestamp"]).total_seconds()) < 5


def test_small_clock_skew_is_tolerated():
    make(**{"@timestamp": (datetime.now(UTC) + timedelta(minutes=1)).isoformat()})


def test_message_trailing_whitespace_is_trimmed():
    assert make(message="boom  \n").message == "boom"


def test_label_values_are_stored_as_keywords():
    log = make(labels={"team": "core", "replicas": 3, "canary": True, "ratio": 0.5})
    assert log.labels == {"team": "core", "replicas": "3", "canary": "true", "ratio": "0.5"}


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"log.level": "   "}, "log.level"),
        ({"log.level": 3}, "log.level"),
        ({"service.name": ""}, "service.name"),
        ({"service.name": "has spaces"}, "service.name"),
        ({"service.name": "-starts-with-dash"}, "service.name"),
        ({"service.name": "a" * 101}, "service.name"),
        ({"message": ""}, "message"),
        ({"message": "   "}, "message"),
        ({"message": "x" * 10_001}, "message"),
        ({"@timestamp": "yesterday"}, "@timestamp"),
        ({"@timestamp": (datetime.now(UTC) + timedelta(hours=1)).isoformat()}, "@timestamp"),
        ({"trace.id": "not a trace id!"}, "trace.id"),
        ({"span.id": "x" * 65}, "span.id"),
        ({"event.dataset": "bad dataset"}, "event.dataset"),
        ({"ecs.version": "latest"}, "ecs.version"),
        ({"labels": {"nested": {"a": 1}}}, "labels.nested"),
        ({"labels": {f"k{i}": "v" for i in range(51)}}, "labels"),
        ({"tags": "not-a-list"}, "tags"),
        ({"tags": [""]}, "tags.0"),
        ({"custom": {"blob": "x" * 9000}}, "__extra__"),
    ],
)
def test_invalid_document_is_rejected(overrides, field):
    locs = errors_for({**BASE, **overrides})
    assert any(loc == field or loc.startswith(field + ".") for loc in locs), locs


@pytest.mark.parametrize("missing", ["service.name", "log.level", "message"])
def test_required_fields(missing):
    data = dict(BASE)
    data.pop(missing)
    assert missing in errors_for(data)


def test_body_must_be_an_object():
    with pytest.raises(ValidationError):
        LogCreate.model_validate(["not", "an", "object"])


def test_batch_limits():
    with pytest.raises(ValidationError):
        LogBatchCreate.model_validate({"logs": []})
    with pytest.raises(ValidationError):
        LogBatchCreate.model_validate({"logs": [BASE] * 1001})
    assert len(LogBatchCreate.model_validate({"logs": [BASE] * 1000}).logs) == 1000


# ------------------------------------------------------------------ ECS output


def _entry(**overrides) -> LogEntry:
    values = dict(
        id=7,
        timestamp=datetime(2026, 11, 16, 13, tzinfo=UTC),
        message="boom",
        log_level="WARN",
        level="warn",
        event_severity=4,
        log_logger=None,
        service_name="orders",
        service_version=None,
        service_environment=None,
        host_name=None,
        trace_id="abc",
        span_id=None,
        transaction_id=None,
        error_type=None,
        error_message=None,
        error_stack_trace=None,
        event_dataset=None,
        event_original=None,
        labels={},
        tags=[],
        ecs_version=None,
        extra={},
        ingested_at=datetime(2026, 11, 16, 13, 0, 1, tzinfo=UTC),
    )
    values.update(overrides)
    return LogEntry(**values)


def test_output_is_a_nested_ecs_document():
    doc = to_ecs_document(_entry())
    assert doc == {
        "id": 7,
        "@timestamp": datetime(2026, 11, 16, 13, tzinfo=UTC),
        "message": "boom",
        "log": {"level": "WARN"},
        "service": {"name": "orders"},
        "trace": {"id": "abc"},
        "event": {"severity": 4, "ingested": datetime(2026, 11, 16, 13, 0, 1, tzinfo=UTC)},
        "ecs": {"version": ECS_VERSION},
    }


def test_output_merges_extra_fields_but_core_fields_win():
    doc = to_ecs_document(
        _entry(extra={"log": {"origin": {"file": {"line": 3}}, "level": "spoofed"}, "x": 1})
    )
    assert doc["log"] == {"level": "WARN", "origin": {"file": {"line": 3}}}
    assert doc["x"] == 1


def test_output_keeps_sender_ecs_version():
    assert to_ecs_document(_entry(ecs_version="8.11.0"))["ecs"] == {"version": "8.11.0"}


def test_log_read_serializes_with_ecs_names():
    body = to_log_read(_entry(labels={"team": "core"})).model_dump(
        mode="json", by_alias=True, exclude_none=True
    )
    assert body["@timestamp"] == "2026-11-16T13:00:00Z"
    assert body["labels"] == {"team": "core"}
    assert "timestamp" not in body


# ------------------------------------------------------------------ filters and analysis requests


def test_filters_use_ecs_names_and_normalize_levels():
    f = LogFilters.model_validate(
        {"service.name": ["Orders"], "log.level": ["WARNING", "err", "Information"]}
    )
    assert f.service_name == ["orders"]
    assert [lvl.value for lvl in f.level] == ["warn", "error", "info"]


def test_filters_reject_unknown_level():
    with pytest.raises(ValidationError):
        LogFilters.model_validate({"log.level": ["LOUD"]})


def test_filters_reject_inverted_range():
    with pytest.raises(ValidationError, match="'from' must be earlier"):
        LogFilters.model_validate({"from": "2026-11-16T10:00:00Z", "to": "2026-11-16T09:00:00Z"})


def test_filters_accept_equal_bounds():
    ts = "2026-11-16T10:00:00Z"
    f = LogFilters.model_validate({"from": ts, "to": ts})
    assert f.from_ == f.to


def test_filters_with_offset_timezones_compare_correctly():
    f = LogFilters.model_validate({"from": "2026-11-16T12:00:00Z", "to": "2026-11-16T10:00:00-03:00"})
    assert f.to == datetime(2026, 11, 16, 13, tzinfo=UTC)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"log_ids": [1], "filters": {"service.name": ["a"]}},
        {"log_ids": []},
        {"log_ids": [0, 1]},
        {"log_ids": [-5]},
        {"log_ids": list(range(1, 502))},
        {"filters": {"limit": 0}},
        {"filters": {"limit": 501}},
        {"filters": {"log.level": ["NOPE"]}},
        {"filters": {"service": ["old-name"]}},
        {"log_ids": [1], "extra": True},
    ],
)
def test_analyze_request_rejects(payload):
    with pytest.raises(ValidationError):
        AnalyzeRequest.model_validate(payload)


def test_analyze_request_deduplicates_ids_preserving_order():
    assert AnalyzeRequest.model_validate({"log_ids": [3, 1, 3, 2, 1]}).log_ids == [3, 1, 2]


def test_analyze_request_with_empty_filters_uses_default_limit():
    assert AnalyzeRequest.model_validate({"filters": {}}).filters.limit == 200


def test_analyze_request_is_stored_with_ecs_names():
    req = AnalyzeRequest.model_validate({"filters": {"service.name": ["a"], "log.level": ["WARN"]}})
    dumped = req.model_dump(mode="json", by_alias=True, exclude_none=True)
    assert dumped == {"filters": {"service.name": ["a"], "log.level": ["warn"], "limit": 200}}


# ------------------------------------------------------------------ nothing is lost at ingestion


def test_unknown_level_is_kept_counted_as_info_and_tagged():
    row = make(
        **{"log.level": "VERBOSE!", "tags": ["checkout"], "@timestamp": "2026-11-16T10:00:00Z"}
    ).to_row()
    assert row["log_level"] == "VERBOSE!"
    assert (row["level"], row["event_severity"]) == ("info", 6)
    assert row["tags"] == ["checkout", "_level_unrecognized"]


def test_known_level_and_timestamp_add_no_processing_tags():
    row = make(**{"@timestamp": "2026-11-16T10:00:00Z"}).to_row()
    assert row["tags"] == []


def test_missing_timestamp_is_tagged():
    assert make().to_row()["tags"] == ["_timestamp_missing"]


def test_processing_tags_are_not_duplicated():
    row = make(tags=["_timestamp_missing"]).to_row()
    assert row["tags"] == ["_timestamp_missing"]


def test_event_id_is_a_core_field():
    log = make(event={"id": "evt-1", "dataset": "payments.app"})
    assert log.event_id == "evt-1"
    assert log.extra == {}, "event.id no longer lands in extra"


def test_original_is_stored_only_when_enabled():
    log = make(**{"@timestamp": "2026-11-16T10:00:00Z", "custom": {"a": 1}})
    assert log.to_row()["event_original"] is None
    original = log.to_row(keep_original=True)["event_original"]
    assert json.loads(original) == {**BASE, "@timestamp": "2026-11-16T10:00:00Z", "custom": {"a": 1}}


def test_original_sent_by_the_source_wins():
    log = make(event={"original": "raw line"})
    assert log.to_row(keep_original=True)["event_original"] == "raw line"


def test_raw_copy_is_not_part_of_the_api_schema():
    assert "__raw__" not in LogCreate.model_json_schema(by_alias=True)["properties"]
    assert "raw" not in make().model_dump()
