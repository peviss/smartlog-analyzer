import pytest

from api.domain import ecs


@pytest.mark.parametrize(
    ("original", "normalized", "severity"),
    [
        ("ERROR", "error", 3),
        ("err", "error", 3),
        ("E", "error", 3),
        ("WARN", "warn", 4),
        ("warning", "warn", 4),
        ("Warning", "warn", 4),
        ("Information", "info", 6),
        ("informational", "info", 6),
        ("notice", "info", 5),
        ("FATAL", "critical", 2),
        ("crit", "critical", 2),
        ("emerg", "critical", 0),
        ("alert", "critical", 1),
        ("SEVERE", "critical", 2),  # java.util.logging
        ("trace", "debug", 7),
        ("FINEST", "debug", 7),
        ("  debug  ", "debug", 7),
    ],
)
def test_normalize_level_maps_common_spellings(original, normalized, severity):
    assert ecs.normalize_level(original) == (normalized, severity)


@pytest.mark.parametrize("bad", ["LOUD", "", "3", "warn!"])
def test_normalize_level_rejects_unknown(bad):
    with pytest.raises(ValueError, match="unknown log level"):
        ecs.normalize_level(bad)


def test_severity_is_lower_for_more_severe_levels():
    order = ["critical", "error", "warn", "info", "debug"]
    assert [ecs.LEVEL_SEVERITY[lvl] for lvl in order] == sorted(ecs.LEVEL_SEVERITY.values())


def test_nest_expands_dotted_keys():
    assert ecs.nest({"log.level": "info", "service.name": "a"}) == {
        "log": {"level": "info"},
        "service": {"name": "a"},
    }


def test_nest_merges_dotted_and_nested_styles():
    doc = {"log.level": "info", "log": {"logger": "x", "origin.file.line": 3}}
    assert ecs.nest(doc) == {"log": {"level": "info", "logger": "x", "origin": {"file": {"line": 3}}}}


def test_nest_accepts_the_same_value_twice():
    assert ecs.nest({"log.level": "info", "log": {"level": "info"}}) == {"log": {"level": "info"}}


def test_nest_rejects_conflicting_values():
    with pytest.raises(ValueError, match="given twice"):
        ecs.nest({"log.level": "info", "log": {"level": "error"}})


def test_nest_rejects_value_and_object_on_same_path():
    with pytest.raises(ValueError, match="both a value and an object"):
        ecs.nest({"service": "payments", "service.name": "payments"})


@pytest.mark.parametrize("key", ["", "a..b", ".a", "a."])
def test_nest_rejects_invalid_field_names(key):
    with pytest.raises(ValueError, match="field name"):
        ecs.nest({key: 1})


def test_nest_does_not_split_label_keys():
    doc = {"labels": {"app.tier": "web"}, "labels.team": "core"}
    assert ecs.nest(doc) == {"labels": {"app.tier": "web", "team": "core"}}


def test_nest_keeps_timestamp_key():
    assert ecs.nest({"@timestamp": "2026-01-01T00:00:00Z"}) == {"@timestamp": "2026-01-01T00:00:00Z"}


def test_pop_path_removes_value_and_prunes_empty_parents():
    doc = {"log": {"level": "info"}, "service": {"name": "a", "version": "1"}}
    assert ecs.pop_path(doc, "log.level") == "info"
    assert "log" not in doc
    assert ecs.pop_path(doc, "service.name") == "a"
    assert doc == {"service": {"version": "1"}}


def test_pop_path_missing():
    assert ecs.is_missing(ecs.pop_path({"log": "flat"}, "log.level"))
    assert ecs.is_missing(ecs.pop_path({}, "trace.id"))


def test_deep_merge_override_wins_and_nested_merges():
    base = {"log": {"origin": {"file": {"line": 3}}, "level": "spoofed"}, "x": 1}
    merged = ecs.deep_merge(base, {"log": {"level": "info"}})
    assert merged == {"log": {"origin": {"file": {"line": 3}}, "level": "info"}, "x": 1}


def test_lenient_normalization_falls_back_to_info():
    assert ecs.normalize_level_lenient("WARNING") == ("warn", 4, True)
    assert ecs.normalize_level_lenient("LOUD") == ("info", 6, False)
