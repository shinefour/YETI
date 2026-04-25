"""Tests for resolution_patterns store + suggestion logic."""

import pytest

from yeti.models.resolution_patterns import (
    ResolutionPatternStore,
    make_pattern_key,
)


@pytest.fixture
def store(tmp_path):
    return ResolutionPatternStore(db_path=tmp_path / "patterns.db")


def test_make_pattern_key_format():
    key = make_pattern_key(
        "proposed_action", "Submit feedback for X in Ashby"
    )
    assert key == "proposed_action::Submit feedback for X in Ashby"


def test_record_first_resolution_count_one(store):
    rec = store.record_resolution("k1", "discarded")
    assert rec["count"] == 1
    assert store.suggestion_for("k1") is None


def test_record_two_consistent_unlocks_suggestion(store):
    store.record_resolution("k1", "discarded")
    store.record_resolution("k1", "discarded")
    sug = store.suggestion_for("k1")
    assert sug is not None
    assert sug["disposition"] == "discarded"
    assert sug["count"] == 2
    assert sug["auto_apply"] is False


def test_record_changing_disposition_resets_count(store):
    store.record_resolution("k1", "discarded")
    store.record_resolution("k1", "discarded")
    store.record_resolution("k1", "answered")
    sug = store.suggestion_for("k1")
    # Count reset to 1 — below confidence threshold.
    assert sug is None
    row = store.get("k1")
    assert row["count"] == 1
    assert row["disposition"] == "answered"


def test_set_auto_apply_persists(store):
    store.record_resolution("k1", "discarded")
    store.record_resolution("k1", "discarded")
    assert store.set_auto_apply("k1", True) is True
    sug = store.suggestion_for("k1")
    assert sug["auto_apply"] is True


def test_set_auto_apply_unknown_pattern(store):
    assert store.set_auto_apply("nope", True) is False


def test_set_auto_apply_preserved_when_disposition_resets(store):
    store.record_resolution("k1", "discarded")
    store.record_resolution("k1", "discarded")
    store.set_auto_apply("k1", True)
    # Disposition changes → count resets but auto_apply preserved
    store.record_resolution("k1", "answered")
    row = store.get("k1")
    assert bool(row["auto_apply"]) is True
