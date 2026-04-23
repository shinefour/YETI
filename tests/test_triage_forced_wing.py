"""Triage must pin storage to note.forced_wing regardless of LLM output."""

import pytest

from yeti.agents import triage
from yeti.models.notes import Note, NoteSource


class _FakeMemory:
    def __init__(self):
        self.stored: list[dict] = []
        self.facts: list[dict] = []

    async def store(self, **kwargs):
        self.stored.append(kwargs)
        return {"id": "drawer-1"}

    async def kg_add(self, **kwargs):
        self.facts.append(kwargs)
        return {}

    async def search(self, **kwargs):
        return {"results": []}

    async def kg_query(self, **kwargs):
        return {"facts": []}


class _FakeInbox:
    def __init__(self):
        self.items: list = []

    def create(self, item):
        self.items.append(item)
        return item

    def has_pending_for_person(self, *a, **kw):
        return False


@pytest.mark.asyncio
async def test_forced_wing_overrides_llm_choice(monkeypatch):
    fake_mem = _FakeMemory()
    fake_inbox = _FakeInbox()
    monkeypatch.setattr(triage, "_memory", fake_mem)
    monkeypatch.setattr(triage, "_inbox", fake_inbox)

    note = Note(
        content="Contract signed with Globalstudio last week.",
        source=NoteSource.EMAIL,
        context="Email received via Outlook mailbox (x). Wing: conetic.",
        forced_wing="conetic",
    )
    llm_result = {
        "type": "email",
        "title": "Contract",
        "wing": "globalstudio",  # LLM tries to cross-route
        "room": "contracts",
        "facts": [],
        "action_items": [],
        "clarifications": [],
        "people_mentioned": [],
    }

    summary = await triage._apply_triage_result(note, llm_result)

    assert len(fake_mem.stored) == 1
    assert fake_mem.stored[0]["wing"] == "conetic"
    assert fake_mem.stored[0]["room"] == "contracts"
    assert "conetic" in summary


@pytest.mark.asyncio
async def test_no_forced_wing_respects_llm_choice(monkeypatch):
    fake_mem = _FakeMemory()
    fake_inbox = _FakeInbox()
    monkeypatch.setattr(triage, "_memory", fake_mem)
    monkeypatch.setattr(triage, "_inbox", fake_inbox)

    note = Note(
        content="Random idea about side project.",
        source=NoteSource.CLI,
    )
    llm_result = {
        "type": "idea",
        "title": "Idea",
        "wing": "ideas",
        "room": "notes",
        "facts": [],
        "action_items": [],
        "clarifications": [],
        "people_mentioned": [],
    }

    await triage._apply_triage_result(note, llm_result)

    assert fake_mem.stored[0]["wing"] == "ideas"
