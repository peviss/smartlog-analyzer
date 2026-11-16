from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from api.domain.patterns import compute_stats, extract_patterns, to_template

T0 = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)


@dataclass
class FakeLog:
    id: int
    service_name: str
    level: str
    message: str
    timestamp: datetime = T0


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Payment failed for order 123456", "Payment failed for order <NUM>"),
        ("Took 532ms to respond", "Took <NUM> to respond"),
        ("Login from 10.0.0.12:8080 rejected", "Login from <IP> rejected"),
        ("user 3f2c9a1e-1b2c-4d5e-8f90-123456789abc not found", "user <UUID> not found"),
        ("sent to jane.doe@example.com", "sent to <EMAIL>"),
        ("GET https://api.stripe.com/v1/charges?x=1 failed", "GET <URL> failed"),
        ("key 'abc' missing", "key '<STR>' missing"),
        ("trace 9f86d081884c7d659a2feaa0c55ad015 dropped", "trace <HEX> dropped"),
        ("   spaces    collapse   ", "spaces collapse"),
        # digits glued to letters are part of a word (http2), ids after "_" are variable (user_42)
        ("http2 call for user_42 failed", "http2 call for user_<NUM> failed"),
    ],
)
def test_to_template_masks_variable_parts(message, expected):
    assert to_template(message) == expected


def test_to_template_uses_only_first_line_of_multiline_messages():
    assert (
        to_template("Traceback (most recent call last):\n  File x.py line 3")
        == "Traceback (most recent call last):"
    )


def test_to_template_handles_empty_message():
    assert to_template("") == ""


def test_to_template_truncates_very_long_messages():
    assert len(to_template("x" * 5000)) == 300


def test_extract_patterns_groups_by_level_and_template_and_counts():
    logs = [
        FakeLog(1, "payments", "error", "Payment failed for order 1"),
        FakeLog(2, "payments", "error", "Payment failed for order 2", T0 + timedelta(minutes=5)),
        FakeLog(3, "orders", "error", "Payment failed for order 3", T0 - timedelta(minutes=5)),
        FakeLog(4, "orders", "warn", "Payment failed for order 4"),  # same text, other level
    ]
    patterns = extract_patterns(logs)
    assert len(patterns) == 2
    error = patterns[0]
    assert error.level == "error"
    assert error.count == 3
    assert dict(error.services) == {"payments": 2, "orders": 1}
    assert error.first_seen == T0 - timedelta(minutes=5)
    assert error.last_seen == T0 + timedelta(minutes=5)


def test_extract_patterns_orders_by_severity_then_frequency_and_limits():
    logs = [FakeLog(i, "svc", "info", "noise") for i in range(10)]
    logs += [FakeLog(100, "svc", "critical", "disk full")]
    logs += [FakeLog(200 + i, "svc", "error", f"boom {i}") for i in range(3)]
    patterns = extract_patterns(logs, max_patterns=2)
    assert [p.level for p in patterns] == ["critical", "error"]


def test_extract_patterns_keeps_limited_samples():
    logs = [FakeLog(i, "svc", "error", f"fail {i}") for i in range(1, 10)]
    (pattern,) = extract_patterns(logs, samples=3)
    assert pattern.sample_ids == [1, 2, 3]
    assert pattern.to_prompt_dict()["count"] == 9


def test_extract_patterns_empty_input():
    assert extract_patterns([]) == []


def test_compute_stats():
    logs = [
        FakeLog(1, "a", "error", "x", T0),
        FakeLog(2, "a", "info", "y", T0 + timedelta(hours=1)),
        FakeLog(3, "b", "critical", "z", T0 - timedelta(hours=1)),
    ]
    stats = compute_stats(logs)
    assert stats["total"] == 3
    assert stats["by_level"] == {"error": 1, "info": 1, "critical": 1}
    assert stats["errors_by_service"] == {"a": 1, "b": 1}
    assert stats["window"]["from"] == (T0 - timedelta(hours=1)).isoformat()


def test_compute_stats_empty():
    assert compute_stats([])["total"] == 0
