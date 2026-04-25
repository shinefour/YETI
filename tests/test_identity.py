"""Tests for contact drawer rendering + auto-materialization."""

import pytest

from yeti.identity import ensure_contact_drawer, render_contact_drawer


def _fact(subject, predicate, obj, direction="outgoing"):
    return {
        "subject": subject,
        "predicate": predicate,
        "object": obj,
        "direction": direction,
    }


def test_render_minimal():
    body = render_contact_drawer(
        "Daniel Costa", [_fact("Daniel Costa", "role", "VP")]
    )
    assert body.startswith("Name: Daniel Costa")
    assert "Role: VP" in body


def test_render_picks_role_company_email_phone():
    facts = [
        _fact("Daniel Costa", "has_role", "VP Finance"),
        _fact("Daniel Costa", "works_at", "Conetic"),
        _fact(
            "Daniel Costa",
            "email",
            "daniel.costa@coneticgroup.com",
        ),
        _fact(
            "Daniel Costa",
            "has_mobile_number",
            "+351969175105",
        ),
    ]
    body = render_contact_drawer("Daniel Costa", facts)
    assert "Role: VP Finance" in body
    assert "Company: Conetic" in body
    assert "Email: daniel.costa@coneticgroup.com" in body
    assert "Phone: +351969175105" in body


def test_render_skips_incoming_facts():
    facts = [
        _fact(
            "Daniel Mundt",
            "is_different_person_from",
            "Daniel Costa",
            direction="incoming",
        ),
        _fact("Daniel Costa", "role", "VP"),
    ]
    body = render_contact_drawer("Daniel Costa", facts)
    assert "Role: VP" in body
    # Incoming "is_different_person_from" should NOT show up as
    # an outgoing other-fact.
    assert "is_different_person_from" not in body


def test_render_extra_facts_under_other():
    facts = [
        _fact("Person", "role", "Manager"),
        _fact(
            "Person",
            "is_different_person_from",
            "Other",
        ),
    ]
    body = render_contact_drawer("Person", facts)
    assert "Other facts:" in body
    assert "is_different_person_from: Other" in body


class _FakeClient:
    def __init__(self, facts):
        self.facts = facts
        self.stored: list[dict] = []

    async def kg_query(self, entity, source="unknown"):
        return {"facts": self.facts}

    async def store(
        self, content, wing, room, source="yeti"
    ):
        self.stored.append(
            {
                "content": content,
                "wing": wing,
                "room": room,
                "source": source,
            }
        )
        return {"drawer_id": "fake-1"}


@pytest.mark.asyncio
async def test_ensure_writes_drawer_when_facts_exist():
    client = _FakeClient(
        [_fact("Daniel Costa", "role", "VP of Finance")]
    )
    drawer_id = await ensure_contact_drawer(
        "Daniel Costa", client=client
    )
    assert drawer_id == "fake-1"
    assert len(client.stored) == 1
    assert client.stored[0]["wing"] == "people"
    assert client.stored[0]["room"] == "contacts"
    assert "Role: VP of Finance" in client.stored[0]["content"]


@pytest.mark.asyncio
async def test_ensure_noops_when_no_facts():
    client = _FakeClient([])
    drawer_id = await ensure_contact_drawer(
        "Unknown Person", client=client
    )
    assert drawer_id is None
    assert client.stored == []


@pytest.mark.asyncio
async def test_ensure_noops_on_blank_name():
    client = _FakeClient(
        [_fact("X", "role", "Y")]
    )
    assert (
        await ensure_contact_drawer("", client=client) is None
    )
    assert client.stored == []


def test_render_self_drawer_basic():
    from yeti.identity import render_self_drawer

    body = render_self_drawer(
        full_name="Daniel Mundt",
        aliases=["Daniel", "Dan"],
        emails=["daniel@globalstudio.com"],
    )
    assert "Name: Daniel Mundt" in body
    assert "Aliases / first-name forms: Daniel, Dan" in body
    assert "Email: daniel@globalstudio.com" in body
    assert "YETI system owner" in body


def test_render_self_drawer_strips_blanks():
    from yeti.identity import render_self_drawer

    body = render_self_drawer(
        full_name="Alice",
        aliases=["", "  ", "Ali"],
        emails=[None, "a@x.io", ""],
    )
    assert "Aliases / first-name forms: Ali" in body
    assert "Email: a@x.io" in body


class _SelfClient:
    def __init__(self, drawers=None):
        self.drawers = drawers or []
        self.stored = []

    async def search_drawers_with_ids(
        self, query, wing=None, room=None, limit=5, source="x"
    ):
        return self.drawers

    async def store(
        self, content, wing, room, source="yeti"
    ):
        self.stored.append(
            {
                "content": content,
                "wing": wing,
                "room": room,
                "source": source,
            }
        )
        return {"drawer_id": "self-1"}


@pytest.mark.asyncio
async def test_self_drawer_present_true():
    from yeti.identity import self_drawer_present

    client = _SelfClient(
        drawers=[
            {
                "id": "1",
                "text": "...",
                "metadata": {"added_by": "self"},
            }
        ]
    )
    assert await self_drawer_present(client=client) is True


@pytest.mark.asyncio
async def test_self_drawer_present_false():
    from yeti.identity import self_drawer_present

    client = _SelfClient(
        drawers=[
            {
                "id": "1",
                "text": "...",
                "metadata": {"added_by": "triage:abc"},
            }
        ]
    )
    assert await self_drawer_present(client=client) is False


@pytest.mark.asyncio
async def test_write_self_drawer_writes_with_source_self():
    from yeti.identity import write_self_drawer

    client = _SelfClient()
    drawer_id = await write_self_drawer(
        full_name="Daniel Mundt",
        aliases=["Daniel"],
        emails=["d@x.io"],
        client=client,
    )
    assert drawer_id == "self-1"
    assert len(client.stored) == 1
    rec = client.stored[0]
    assert rec["wing"] == "people"
    assert rec["room"] == "contacts"
    assert rec["source"] == "self"
    assert "Name: Daniel Mundt" in rec["content"]


@pytest.mark.asyncio
async def test_write_self_drawer_blank_name_noops():
    from yeti.identity import write_self_drawer

    client = _SelfClient()
    assert await write_self_drawer("", client=client) is None
    assert client.stored == []
