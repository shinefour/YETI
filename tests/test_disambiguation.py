"""Tests for the disambiguation flow."""

from unittest.mock import AsyncMock

import pytest

from yeti.agents import triage
from yeti.models.inbox import InboxStore, InboxType
from yeti.models.notes import Note, NoteSource


@pytest.fixture
def inbox_store(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    store = InboxStore(db)
    monkeypatch.setattr(triage, "_inbox", store)
    return store


def _make_note(title="Test"):
    return Note(
        content="dummy",
        source=NoteSource.API,
        title=title,
    )


@pytest.mark.asyncio
async def test_unknown_person_creates_inbox(
    inbox_store, monkeypatch
):
    monkeypatch.setattr(
        triage,
        "_find_person_matches",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        triage,
        "_check_learned_mapping",
        AsyncMock(return_value=None),
    )

    note = _make_note()
    count = await triage._resolve_people(
        ["Unknown"], "conetic", note
    )
    assert count == 1
    items = inbox_store.list_pending()
    assert len(items) == 1
    assert items[0].type == InboxType.PERSON_UPDATE
    assert "Unknown" in items[0].title


@pytest.mark.asyncio
async def test_single_match_no_inbox(
    inbox_store, monkeypatch
):
    monkeypatch.setattr(
        triage,
        "_find_person_matches",
        AsyncMock(
            return_value=[
                {"text": "Name: Joe Carreira"}
            ]
        ),
    )
    monkeypatch.setattr(
        triage,
        "_check_learned_mapping",
        AsyncMock(return_value=None),
    )

    count = await triage._resolve_people(
        ["Joe"], "conetic", _make_note()
    )
    assert count == 0
    assert inbox_store.count_pending() == 0


@pytest.mark.asyncio
async def test_multiple_matches_creates_disambiguation(
    inbox_store, monkeypatch
):
    monkeypatch.setattr(
        triage,
        "_find_person_matches",
        AsyncMock(
            return_value=[
                {"text": "Name: Michal Zawada", "wing": "people"},
                {
                    "text": "Name: Michal Martika",
                    "wing": "people",
                },
            ]
        ),
    )
    monkeypatch.setattr(
        triage,
        "_check_learned_mapping",
        AsyncMock(return_value=None),
    )

    count = await triage._resolve_people(
        ["Michal"], "conetic", _make_note()
    )
    assert count == 1
    items = inbox_store.list_pending()
    assert items[0].type == InboxType.DISAMBIGUATION
    assert items[0].payload["name"] == "Michal"
    assert items[0].payload["wing_context"] == "conetic"
    assert len(items[0].payload["candidates"]) == 2


@pytest.mark.asyncio
async def test_learned_mapping_skips_disambiguation(
    inbox_store, monkeypatch
):
    monkeypatch.setattr(
        triage,
        "_check_learned_mapping",
        AsyncMock(return_value="Michal Zawada"),
    )
    # find_person_matches should not even be called
    find_mock = AsyncMock(return_value=[])
    monkeypatch.setattr(
        triage, "_find_person_matches", find_mock
    )

    count = await triage._resolve_people(
        ["Michal"], "conetic", _make_note()
    )
    assert count == 0
    find_mock.assert_not_awaited()
