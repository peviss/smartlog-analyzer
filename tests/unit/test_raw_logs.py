from datetime import UTC, datetime

import pytest

from api.domain.raw_logs import (
    TAG_LEVEL_MISSING,
    apply_aliases,
    apply_defaults,
    detect_format,
    parse_csv,
    parse_json,
    parse_line,
    parse_text,
)
from api.schemas.logs import LogCreate

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)


# ------------------------------------------------------------------ plain text lines


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (  # logback
            "2026-11-16 10:00:00,123 ERROR [main] c.e.Payments - Payment failed",
            {
                "@timestamp": "2026-11-16T10:00:00.123",
                "log.level": "ERROR",
                "process.thread.name": "main",
                "log.logger": "c.e.Payments",
                "message": "Payment failed",
            },
        ),
        (  # log4j
            "2026-11-16 10:00:00,123 [main] WARN c.e.Orders - Slow",
            {
                "@timestamp": "2026-11-16T10:00:00.123",
                "log.level": "WARN",
                "process.thread.name": "main",
                "log.logger": "c.e.Orders",
                "message": "Slow",
            },
        ),
        (
            "[2026-11-16T10:00:00Z] [WARN] slow query",
            {"@timestamp": "2026-11-16T10:00:00Z", "log.level": "WARN", "message": "slow query"},
        ),
        (  # python logging: "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            "2026-11-16 10:00:00,001 - payments - ERROR - card declined",
            {
                "@timestamp": "2026-11-16T10:00:00.001",
                "log.level": "ERROR",
                "log.logger": "payments",
                "message": "card declined",
            },
        ),
        ("ERROR: disk full", {"log.level": "ERROR", "message": "disk full"}),
        ("[W] disk almost full", {"log.level": "W", "message": "disk almost full"}),
    ],
)
def test_generic_lines(line, expected):
    assert parse_line(line, NOW) == expected


@pytest.mark.parametrize(
    "line",
    [
        "Info about the user was updated",  # capitalized word at the start, no timestamp
        "2026-11-16T10:00:00Z payments - connection error while saving",  # level word inside the message
        "e-mail sent",  # single letter is not a level unless bracketed
    ],
)
def test_words_in_the_message_are_not_taken_as_levels(line):
    assert "log.level" not in parse_line(line, NOW)


def test_syslog_rfc5424():
    doc = parse_line("<34>1 2026-11-16T10:00:00Z web-1 payments 123 ID47 - Payment declined", NOW)
    assert doc == {
        "@timestamp": "2026-11-16T10:00:00Z",
        "host.name": "web-1",
        "service.name": "payments",
        "log.level": "crit",  # PRI 34 = facility 4, severity 2
        "process.pid": 123,
        "message": "Payment declined",
    }


def test_syslog_rfc5424_with_structured_data_and_nil_values():
    doc = parse_line('<165>1 - - app - - [exampleSDID@32473 iut="3"] started', NOW)
    assert doc == {"service.name": "app", "log.level": "notice", "message": "started"}


def test_syslog_rfc3164_assumes_the_current_year():
    doc = parse_line("<11>Nov 16 10:00:00 web-1 sshd[42]: Failed password for root", NOW)
    assert doc["@timestamp"] == "2026-11-16T10:00:00+00:00"
    assert (doc["service.name"], doc["host.name"], doc["log.level"]) == ("sshd", "web-1", "err")


def test_syslog_rfc3164_in_the_future_is_last_year():
    assert parse_line("Dec 31 23:00:00 h app: x", NOW)["@timestamp"].startswith("2025-12-31")


def test_syslog_rfc3164_without_priority_reads_the_level_from_the_message():
    doc = parse_line("Nov 16 10:00:00 web-1 cron[42]: WARNING job took long", NOW)
    assert (doc["log.level"], doc["message"]) == ("WARNING", "job took long")


def test_logfmt_keeps_unicode_and_escapes():
    doc = parse_line(
        'time=2026-11-16T10:00:00Z level=warn service=orders msg="tarjeta inválida \\"x\\""', NOW
    )
    assert doc == {
        "@timestamp": "2026-11-16T10:00:00Z",
        "log.level": "warn",
        "service.name": "orders",
        "message": 'tarjeta inválida "x"',
    }


def test_json_line_with_pino_numeric_level():
    doc = parse_line('{"level":50,"time":"2026-11-16T10:00:00Z","msg":"boom","hostname":"h1"}', NOW)
    assert doc == {
        "log.level": "error",
        "@timestamp": "2026-11-16T10:00:00Z",
        "message": "boom",
        "host.name": "h1",
    }


def test_unrecognized_line_is_all_message():
    assert parse_line("just some words", NOW) == {"message": "just some words"}


# ------------------------------------------------------------------ plain text files


def test_stack_traces_join_the_previous_record_and_raw_lines_are_kept():
    text = (
        "2026-11-16T10:00:00Z ERROR NullPointerException\n"
        "\tat a.b.C(C.java:1)\n"
        "Caused by: java.io.IOException\n"
        "\n"
        "2026-11-16T10:00:01Z INFO ok\n"
    )
    first, second = parse_text(text, NOW)
    assert (first.line, second.line) == (1, 5)
    assert first.doc["error.stack_trace"] == "\tat a.b.C(C.java:1)\nCaused by: java.io.IOException"
    assert first.doc["event.original"] == text.split("\n\n")[0]
    assert second.doc["event.original"] == "2026-11-16T10:00:01Z INFO ok"


def test_parsed_text_becomes_a_valid_ecs_document():
    (record,) = parse_text("2026-11-16T10:00:00Z ERROR boom", NOW)
    log = LogCreate.model_validate(apply_defaults(record.doc, service_name="payments", log_level=None))
    assert (log.log_level, log.message, log.event_original) == (
        "ERROR",
        "boom",
        "2026-11-16T10:00:00Z ERROR boom",
    )


# ------------------------------------------------------------------ JSON


def test_json_array_object_and_batch_body():
    assert [r.doc for r in parse_json('[{"msg": "a"}, {"msg": "b"}]')] == [{"message": "a"}, {"message": "b"}]
    assert [r.doc for r in parse_json('{"logs": [{"msg": "a"}]}')] == [{"message": "a"}]
    (single,) = parse_json('{"message": "a"}')
    assert (single.doc, single.line) == ({"message": "a"}, 1)


def test_ndjson_reports_bad_lines_with_their_number():
    records = parse_json('{"msg": "a"}\nnot json\n\n{"msg": "b"}')
    assert [(r.line, r.error is not None) for r in records] == [(1, False), (2, True), (4, False)]


def test_non_object_items_are_left_for_validation():
    assert parse_json("[3]")[0].doc == 3


# ------------------------------------------------------------------ CSV


def test_csv_with_aliases_semicolons_and_empty_cells():
    text = "timestamp;level;service;message;labels.team;tags;host.name\n2026-11-16T10:00:00Z;ERROR;payments;boom;core;a,b;\n"
    (record,) = parse_csv(text)
    assert record.line == 2
    assert record.doc == {
        "@timestamp": "2026-11-16T10:00:00Z",
        "log.level": "ERROR",
        "service.name": "payments",
        "message": "boom",
        "labels": {"team": "core"},
        "tags": ["a", "b"],
    }


def test_csv_quoted_multiline_cells_keep_line_numbers():
    text = 'message,level\n"line one\nline two",error\nok,info\n'
    first, second = parse_csv(text)
    assert first.doc["message"] == "line one\nline two"
    assert (first.line, second.line) == (2, 4)


def test_csv_reports_rows_with_extra_columns_and_skips_empty_rows():
    records = parse_csv("message,level\na,info\n,\nb,info,extra\n")
    assert [(r.line, r.error) for r in records] == [
        (2, None),
        (4, "the row has more columns than the header"),
    ]


# ------------------------------------------------------------------ aliases, defaults, format


def test_ecs_names_win_over_aliases():
    assert apply_aliases({"message": "ecs", "msg": "alias", "log": {"level": "info"}, "level": "error"}) == {
        "message": "ecs",
        "log": {"level": "info"},
    }


def test_host_object_is_not_treated_as_an_alias():
    assert apply_aliases({"host": {"name": "h"}}) == {"host": {"name": "h"}}


def test_defaults_fill_missing_fields_and_tag_the_assumed_level():
    doc = apply_defaults({"message": "x", "tags": ["t"]}, service_name="svc", log_level="warn")
    assert doc == {
        "message": "x",
        "service.name": "svc",
        "log.level": "warn",
        "tags": ["t", TAG_LEVEL_MISSING],
    }


def test_defaults_never_override_the_record():
    doc = apply_defaults(
        {"service": {"name": "own"}, "log.level": "error"}, service_name="svc", log_level="warn"
    )
    assert doc == {"service": {"name": "own"}, "log.level": "error"}


@pytest.mark.parametrize(
    ("content_type", "explicit", "expected"),
    [
        ("text/csv; charset=utf-8", None, "csv"),
        ("application/x-ndjson", None, "json"),
        ("application/json", None, "json"),
        ("text/plain", None, "text"),
        (None, None, "text"),
        ("text/plain", "csv", "csv"),
    ],
)
def test_detect_format(content_type, explicit, expected):
    assert detect_format(content_type, explicit) == expected
