"""Non-ECS schemas and formats: known profiles, generic normalization and timestamps."""

import json
from datetime import UTC, datetime

import pytest

from api.domain.raw_logs import (
    TAG_FIELDS_DROPPED,
    TAG_MESSAGE_MISSING,
    TAG_TIMESTAMP_UNPARSED,
    TAG_TRUNCATED,
    apply_defaults,
    detect_profile,
    normalize_document,
    parse_json,
    parse_line,
    parse_text,
)
from api.domain.raw_logs.timestamps import parse_timestamp
from api.schemas.logs import LogCreate

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)


def one(payload: dict) -> dict:
    (record,) = parse_json(json.dumps(payload))
    return record.doc


def valid(doc: dict) -> LogCreate:
    """Whatever the source, the normalized record must pass the ECS validation."""
    return LogCreate.model_validate(apply_defaults(dict(doc), service_name="fallback", log_level=None))


# ------------------------------------------------------------------ timestamps


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1763287200, "2025-11-16T10:00:00+00:00"),  # seconds
        (1763287200123, "2025-11-16T10:00:00.123000+00:00"),  # milliseconds
        ("1763287200000000", "2025-11-16T10:00:00+00:00"),  # microseconds, as text
        ("1763287200000000000", "2025-11-16T10:00:00+00:00"),  # nanoseconds (OpenTelemetry)
        ("2026-11-16 10:00:00,5", "2026-11-16T10:00:00.5"),
        ("2026-11-16T10:00:00.123456789Z", "2026-11-16T10:00:00.123456Z"),  # Go / Kubernetes
        ("2026-11-16 10:00:00 UTC", "2026-11-16T10:00:00Z"),
        ("16/Nov/2026:10:00:00 +0000", "2026-11-16T10:00:00+00:00"),  # Apache
        ("2026/11/16 10:00:00", "2026-11-16T10:00:00"),  # nginx error log
        ("Nov 16, 2026 10:00:00 AM", "2026-11-16T10:00:00"),  # Java default
        ("16-Nov-2026 10:00:00.250", "2026-11-16T10:00:00.250000"),  # Tomcat
        ("Mon, 16 Nov 2026 10:00:00 +0000", "2026-11-16T10:00:00+00:00"),  # RFC 2822
    ],
)
def test_timestamp_formats(value, expected):
    assert parse_timestamp(value) == expected


@pytest.mark.parametrize("value", ["yesterday", "", 42, True, None, "2026-13-45T99:00:00"])
def test_unreadable_timestamps(value):
    assert parse_timestamp(value) is None


# ------------------------------------------------------------------ known schemas


def test_opentelemetry_export_expands_every_record_with_its_resource():
    payload = {
        "resourceLogs": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": "checkout"}},
                        {"key": "deployment.environment.name", "value": {"stringValue": "prod"}},
                    ]
                },
                "scopeLogs": [
                    {
                        "scope": {"name": "com.example.Checkout"},
                        "logRecords": [
                            {
                                "timeUnixNano": "1763287200000000000",
                                "severityNumber": 17,
                                "body": {"stringValue": "Payment failed"},
                                "traceId": "5b8efff798038103d269b633813fc60c",
                                "attributes": [
                                    {"key": "exception.type", "value": {"stringValue": "IOException"}},
                                    {"key": "http.request.method", "value": {"stringValue": "POST"}},
                                ],
                            },
                            {
                                "observedTimeUnixNano": "1763287201000000000",
                                "body": {
                                    "kvlistValue": {"values": [{"key": "a", "value": {"intValue": "1"}}]}
                                },
                            },
                        ],
                    }
                ],
            }
        ]
    }
    first, second = (r.doc for r in parse_json(json.dumps(payload)))
    assert detect_profile(payload) == "opentelemetry"
    assert first == {
        "service.name": "checkout",
        "service.environment": "prod",
        "@timestamp": "2025-11-16T10:00:00+00:00",
        "log.level": "error",  # severityNumber 17-20
        "message": "Payment failed",
        "trace.id": "5b8efff798038103d269b633813fc60c",
        "error.type": "IOException",
        "http.request.method": "POST",
        "log.logger": "com.example.Checkout",
    }
    assert second["message"] == '{"a": 1}'
    assert all(valid(r) for r in (first, second))


def test_gelf():
    doc = one(
        {
            "version": "1.1",
            "host": "web-1",
            "short_message": "Disk full",
            "full_message": "Disk full\nat /var/lib",
            "timestamp": 1763287200.5,
            "level": 2,
            "_service": "storage",
            "_user_id": 9,
        }
    )
    assert doc == {
        "message": "Disk full",
        "error.stack_trace": "Disk full\nat /var/lib",
        "host.name": "web-1",
        "@timestamp": "2025-11-16T10:00:00.500000+00:00",
        "log.level": "crit",
        "service.name": "storage",
        "labels": {"user_id": "9"},
    }


def test_serilog_clef_renders_the_message_template():
    doc = one(
        {
            "@t": "2026-11-16T10:00:00Z",
            "@mt": "Order {OrderId} failed for {@Customer} ({{literal}})",
            "@l": "Error",
            "@x": "System.InvalidOperationException: boom\n   at Orders.Pay()",
            "OrderId": 7,
            "Customer": {"Id": 3},
            "SourceContext": "Orders.Service",
            "Application": "orders",
        }
    )
    assert doc["message"] == 'Order 7 failed for {"Id": 3} ({literal})'
    assert (doc["log.level"], doc["log.logger"], doc["service.name"]) == ("Error", "Orders.Service", "orders")
    assert doc["error.type"] == "System.InvalidOperationException"
    assert doc["Customer.Id"] == 3


def test_clef_without_level_is_information():
    assert one({"@t": "2026-11-16T10:00:00Z", "@m": "Started"})["log.level"] == "Information"


def test_docker_json_file_parses_the_wrapped_line():
    doc = one(
        {"log": "2026-11-16T10:00:00Z ERROR boom\n", "stream": "stderr", "time": "2026-11-16T10:00:00.5Z"}
    )
    assert doc == {
        "@timestamp": "2026-11-16T10:00:00Z",
        "log.level": "ERROR",
        "message": "boom",
        "labels": {"stream": "stderr"},
    }


def test_docker_with_a_json_application_line():
    doc = one(
        {
            "log": '{"level":"warn","msg":"slow","service":"api"}\n',
            "stream": "stdout",
            "time": "2026-11-16T10:00:00Z",
        }
    )
    assert (doc["log.level"], doc["message"], doc["service.name"]) == ("warn", "slow", "api")


def test_google_cloud_logging_json_payload_and_http_request():
    doc = one(
        {
            "insertId": "abc",
            "severity": "WARNING",
            "timestamp": "2026-11-16T10:00:00Z",
            "logName": "projects/p/logs/cloudaudit.googleapis.com%2Factivity",
            "trace": "projects/p/traces/4bf92f3577b34da6a3ce929d0e0e4736",
            "resource": {
                "type": "cloud_run_revision",
                "labels": {"service_name": "checkout", "project_id": "p", "location": "us-east1"},
            },
            "httpRequest": {"requestMethod": "GET", "requestUrl": "/cart", "status": 503},
            "jsonPayload": {"message": "Upstream down", "retry": 2},
        }
    )
    assert doc["message"] == "Upstream down"
    assert (doc["service.name"], doc["log.level"], doc["event.id"]) == ("checkout", "WARNING", "abc")
    assert doc["trace.id"] == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert doc["event.dataset"] == "cloudaudit.googleapis.com_2Factivity"
    assert (doc["http.response.status_code"], doc["cloud.region"], doc["retry"]) == (503, "us-east1", 2)
    assert valid(doc)


def test_google_cloud_default_severity_means_no_level():
    doc = one({"severity": "DEFAULT", "textPayload": "hello", "logName": "projects/p/logs/x"})
    assert "log.level" not in doc and doc["message"] == "hello"


def test_cloudwatch_subscription_expands_events_and_parses_their_messages():
    payload = {
        "messageType": "DATA_MESSAGE",
        "owner": "123456789012",
        "logGroup": "/aws/lambda/payments",
        "logStream": "2026/11/16/[$LATEST]abc",
        "logEvents": [
            {"id": "1", "timestamp": 1763287200000, "message": '{"level":"error","msg":"declined"}'},
            {"id": "2", "timestamp": 1763287201000, "message": "START RequestId: x Version: $LATEST"},
        ],
    }
    first, second = (r.doc for r in parse_json(json.dumps(payload)))
    assert (first["service.name"], first["log.level"], first["message"]) == ("payments", "error", "declined")
    assert first["labels"] == {"log_group": "/aws/lambda/payments", "log_stream": "2026/11/16/[$LATEST]abc"}
    assert (first["cloud.account.id"], first["event.id"]) == ("123456789012", "1")
    assert second["message"] == "START RequestId: x Version: $LATEST"


def test_cloudwatch_control_messages_have_no_records():
    assert parse_json(json.dumps({"messageType": "CONTROL_MESSAGE", "logEvents": [{"message": "x"}]})) == []


# ------------------------------------------------------------------ generic normalization


def test_any_naming_style_maps_to_ecs():
    doc = normalize_document(
        {
            "Message": "x",
            "logLevel": "WARN",
            "serviceName": "svc",
            "TraceId": "t1",
            "Timestamp": "2026-11-16T10:00:00Z",
            "ThreadName": "main",
        }
    )
    assert doc == {
        "message": "x",
        "log.level": "WARN",
        "service.name": "svc",
        "trace.id": "t1",
        "@timestamp": "2026-11-16T10:00:00Z",
        "process.thread.name": "main",
    }


def test_nested_custom_objects_are_flattened_and_kept():
    doc = normalize_document(
        {"msg": "x", "context": {"user": {"id": 7}, "tags": ["a"]}, "service": {"name": "svc"}}
    )
    assert doc == {"message": "x", "context.user.id": 7, "context.tags": ["a"], "service.name": "svc"}
    assert valid(doc).extra == {"context": {"user": {"id": 7}, "tags": ["a"]}}


@pytest.mark.parametrize(
    ("record", "field", "value"),
    [
        ({"message": "x", "error": "connection refused"}, "error.message", "connection refused"),
        ({"message": "x", "event": "login"}, "event.action", "login"),
        ({"message": "x", "trace": "abc"}, "trace.id", "abc"),
        ({"log": "the line"}, "message", "the line"),  # Docker-like key with no other message
    ],
)
def test_plain_values_in_ecs_object_slots_are_moved(record, field, value):
    doc = normalize_document(record)
    assert doc[field] == value
    assert valid(doc)


def test_a_value_that_is_also_a_prefix_keeps_both():
    doc = normalize_document({"message": "x", "db": "orders", "db.statement": "SELECT 1"})
    assert (doc["db.value"], doc["db.statement"]) == ("orders", "SELECT 1")
    assert valid(doc)


def test_labels_are_flattened_to_strings():
    doc = normalize_document(
        {"message": "x", "labels": {"team": "core", "k8s": {"pod": "p-1"}, "ok": True, "ids": [1, 2]}}
    )
    assert doc["labels"] == {"team": "core", "k8s.pod": "p-1", "ok": "true", "ids": "1,2"}
    assert valid(doc)


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        ("a, b;c", ["a", "b", "c"]),
        (["a", 1, None, "a"], ["a", "1"]),
        ({"env": "prod"}, ["env:prod"]),
        (5, ["5"]),
    ],
)
def test_tags_in_any_shape(tags, expected):
    assert normalize_document({"message": "x", "tags": tags})["tags"] == expected


def test_a_record_without_a_message_becomes_its_own_message():
    record = {"level": "warn", "user": {"id": 1}, "action": "login"}
    doc = normalize_document(record)
    assert json.loads(doc["message"]) == record
    assert TAG_MESSAGE_MISSING in doc["tags"]


def test_message_fallback_fields():
    assert normalize_document({"body": "from body", "level": "info"})["message"] == "from body"


def test_message_objects_are_serialized():
    assert normalize_document({"message": {"a": 1}})["message"] == '{"a": 1}'


def test_long_messages_are_truncated_and_kept_whole_in_the_original():
    doc = normalize_document({"message": "x" * 12_000})
    assert len(doc["message"]) == 10_000 and len(doc["event.original"]) == 12_000
    assert TAG_TRUNCATED in doc["tags"]
    assert valid(doc)


def test_too_many_extra_fields_drop_the_largest_and_keep_the_record():
    record = {"message": "x", "small": 1, "big": "y" * 9_000}
    doc = normalize_document(record)
    assert "big" not in doc and doc["small"] == 1
    assert json.loads(doc["event.original"]) == record
    assert TAG_FIELDS_DROPPED in doc["tags"]
    assert valid(doc)


def test_unreadable_timestamp_is_kept_as_a_label():
    doc = normalize_document({"message": "x", "time": "next tuesday"})
    assert "@timestamp" not in doc
    assert doc["labels"] == {"original_timestamp": "next tuesday"}
    assert TAG_TIMESTAMP_UNPARSED in doc["tags"]


@pytest.mark.parametrize(
    ("level", "expected"), [(50, "error"), ("30", "info"), (3, "err"), ("  warn ", "warn")]
)
def test_numeric_and_padded_levels(level, expected):
    assert normalize_document({"message": "x", "level": level})["log.level"] == expected


def test_service_names_are_made_valid_and_the_original_kept():
    doc = normalize_document({"message": "x", "service": "Payments API (EU)"})
    assert doc["service.name"] == "payments-api-eu"
    assert doc["labels"] == {"original_service_name": "Payments API (EU)"}


def test_invalid_ids_move_to_labels():
    doc = normalize_document({"message": "x", "trace_id": "projects/p/traces/1", "span_id": "ok-1"})
    assert doc["span.id"] == "ok-1" and "trace.id" not in doc
    assert doc["labels"] == {"trace_id": "projects/p/traces/1"}


def test_ecs_documents_pass_through_unchanged():
    ecs_doc = {
        "@timestamp": "2026-11-16T10:00:00Z",
        "log.level": "error",
        "message": "boom",
        "service.name": "payments",
        "labels": {"team": "core"},
        "tags": ["checkout"],
    }
    assert normalize_document(dict(ecs_doc)) == ecs_doc


# ------------------------------------------------------------------ plain-text formats


def test_combined_access_log():
    doc = parse_line(
        '10.0.0.1 - bob [16/Nov/2026:10:00:00 +0000] "POST /pay HTTP/1.1" 502 17 "https://shop" "curl/8"', NOW
    )
    assert doc == {
        "@timestamp": "2026-11-16T10:00:00+00:00",
        "source.ip": "10.0.0.1",
        "user.name": "bob",
        "http.request.method": "POST",
        "url.original": "/pay",
        "http.version": "1.1",
        "http.response.status_code": 502,
        "http.response.body.bytes": 17,
        "http.request.referrer": "https://shop",
        "user_agent.original": "curl/8",
        "event.category": "web",
        "log.level": "error",  # 5xx
        "message": "POST /pay HTTP/1.1 502",
    }


def test_common_access_log_with_4xx_is_a_warning():
    doc = parse_line('10.0.0.1 - - [16/Nov/2026:10:00:00 +0000] "GET /x HTTP/1.0" 404 -', NOW)
    assert (doc["log.level"], doc["http.response.status_code"]) == ("warn", 404)
    assert "user.name" not in doc and "http.response.body.bytes" not in doc


def test_klog():
    doc = parse_line("W1116 09:59:58.123456   42 reflector.go:324] watch closed", NOW)
    assert doc == {
        "@timestamp": "2026-11-16T09:59:58.123456+00:00",
        "log.level": "warn",
        "process.pid": 42,
        "log.origin.file.name": "reflector.go",
        "log.origin.file.line": 324,
        "message": "watch closed",
    }


def test_kubernetes_cri_line_with_json_inside():
    doc = parse_line('2026-11-16T10:00:00.1Z stderr F {"level":"error","msg":"inner","user":"u1"}', NOW)
    assert (doc["log.level"], doc["message"], doc["user.name"], doc["labels"]) == (
        "error",
        "inner",
        "u1",
        {"stream": "stderr"},
    )


def test_syslog_wrapping_logfmt_keeps_the_inner_level():
    doc = parse_line('<14>1 2026-11-16T10:00:00Z h app - - - level=warn msg="via syslog"', NOW)
    assert (doc["log.level"], doc["message"]) == ("warn", "via syslog")


@pytest.mark.parametrize(
    "line",
    [
        "2026/11/16 10:00:00 [error] 12#12: upstream timed out",  # nginx error log
        "16-Nov-2026 10:00:00.250 SEVERE [main] org.apache.Catalina - failed",  # Tomcat
        "1763287200.25 ERROR worker crashed",  # epoch seconds prefix
    ],
)
def test_other_timestamp_prefixes(line):
    doc = parse_line(line, NOW)
    assert "@timestamp" in doc and "log.level" in doc


def test_mixed_file_every_record_is_valid():
    text = "\n".join(
        [
            '{"severity":"ERROR","textPayload":"gcp","logName":"projects/p/logs/x"}',
            '{"@t":"2026-11-16T10:00:00Z","@mt":"clef {N}","N":1}',
            '{"version":"1.1","host":"h","short_message":"gelf","level":6}',
            '10.0.0.1 - - [16/Nov/2026:10:00:00 +0000] "GET / HTTP/1.1" 200 5',
            "E1116 10:00:00.000001 1 x.go:1] klog",
            "2026-11-16T10:00:00.1Z stdout F plain inside cri",
            "<11>Nov 16 10:00:00 web-1 sshd[42]: syslog",
            "level=info msg=logfmt",
            "anything at all",
        ]
    )
    records = parse_text(text, NOW)
    assert len(records) == 9
    for record in records:
        valid(record.doc)
