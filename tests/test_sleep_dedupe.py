"""Tests for sleep dedupe and the supersession store."""

from unittest.mock import patch

import pytest

from yeti.models.superseded import SupersededStore


@pytest.fixture
def supersede_store(tmp_path, monkeypatch):
    db = tmp_path / "supersede.db"
    monkeypatch.setattr(
        "yeti.models.superseded.DB_PATH", db
    )
    return SupersededStore(db_path=db)


def test_supersede_stores_record(supersede_store):
    supersede_store.supersede(
        "old-1", "new-1", "exact-text-duplicate"
    )
    assert supersede_store.is_superseded("old-1")
    assert not supersede_store.is_superseded("new-1")
    assert "old-1" in supersede_store.superseded_ids()
    assert supersede_store.count() == 1


def test_supersede_skips_blank(supersede_store):
    supersede_store.supersede("", "x", "r")
    supersede_store.supersede("x", "", "r")
    assert supersede_store.count() == 0


def test_supersede_replaces_on_duplicate_id(supersede_store):
    supersede_store.supersede("old-1", "new-1", "first")
    supersede_store.supersede("old-1", "new-2", "second")
    assert supersede_store.count() == 1


def test_run_dedupe_collapses_exact_text(
    supersede_store, monkeypatch
):
    """Three drawers, two identical, one different → one supersession."""
    fake_drawers = [
        {
            "id": "a",
            "text": "Name: Daniel\nRole: A",
            "metadata": {
                "wing": "people",
                "room": "contacts",
                "created_at": "2026-04-01",
            },
        },
        {
            "id": "b",
            "text": "Name: Daniel\nRole: A",
            "metadata": {
                "wing": "people",
                "room": "contacts",
                "created_at": "2026-04-15",
            },
        },
        {
            "id": "c",
            "text": "Name: Sonia\nRole: Different",
            "metadata": {
                "wing": "people",
                "room": "contacts",
                "created_at": "2026-04-10",
            },
        },
    ]

    from yeti.sleep import dedupe

    monkeypatch.setattr(
        dedupe, "_enumerate_drawers", lambda: fake_drawers
    )
    # Make dedupe use our supersede_store rather than DB_PATH default.
    with patch.object(
        dedupe, "SupersededStore", return_value=supersede_store
    ):
        result = dedupe.run_dedupe()

    assert result == {"groups": 1, "superseded": 1}
    # 'a' is older than 'b' so 'a' is superseded by 'b'.
    assert supersede_store.is_superseded("a")
    assert not supersede_store.is_superseded("b")
    assert not supersede_store.is_superseded("c")


def test_run_dedupe_ignores_already_superseded(
    supersede_store, monkeypatch
):
    fake_drawers = [
        {
            "id": "a",
            "text": "duplicate body",
            "metadata": {
                "wing": "x",
                "room": "y",
                "created_at": "2026-04-01",
            },
        },
        {
            "id": "b",
            "text": "duplicate body",
            "metadata": {
                "wing": "x",
                "room": "y",
                "created_at": "2026-04-02",
            },
        },
    ]
    supersede_store.supersede("a", "b", "previous")
    starting_count = supersede_store.count()

    from yeti.sleep import dedupe

    monkeypatch.setattr(
        dedupe, "_enumerate_drawers", lambda: fake_drawers
    )
    with patch.object(
        dedupe, "SupersededStore", return_value=supersede_store
    ):
        result = dedupe.run_dedupe()

    # No new supersessions on the second run.
    assert result["superseded"] == 0
    assert supersede_store.count() == starting_count


def test_run_dedupe_skips_empty_content(
    supersede_store, monkeypatch
):
    fake_drawers = [
        {
            "id": "a",
            "text": "",
            "metadata": {"wing": "x", "room": "y"},
        },
        {
            "id": "b",
            "text": "",
            "metadata": {"wing": "x", "room": "y"},
        },
    ]

    from yeti.sleep import dedupe

    monkeypatch.setattr(
        dedupe, "_enumerate_drawers", lambda: fake_drawers
    )
    with patch.object(
        dedupe, "SupersededStore", return_value=supersede_store
    ):
        result = dedupe.run_dedupe()

    assert result == {"groups": 0, "superseded": 0}
