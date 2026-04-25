"""Tests for KG fact reconcile."""

import pytest

from yeti.sleep import reconcile


def _f(subject, predicate, obj, valid_from=None, **extra):
    base = {
        "subject": subject,
        "predicate": predicate,
        "object": obj,
        "valid_from": valid_from,
        "valid_to": None,
        "current": True,
        "direction": "outgoing",
    }
    base.update(extra)
    return base


class _FakeClient:
    def __init__(self, fact_store, drawers=None):
        self.fact_store = fact_store
        self.drawers = drawers or []
        self.invalidated: list[dict] = []

    async def search_drawers_with_ids(
        self, query, wing=None, room=None, limit=5, source="x"
    ):
        return self.drawers

    async def kg_query(self, entity, source="x"):
        return {
            "facts": list(
                self.fact_store.get(entity.lower(), [])
            )
        }

    async def kg_invalidate(
        self, subject, predicate, obj, ended=None
    ):
        self.invalidated.append(
            {
                "subject": subject,
                "predicate": predicate,
                "object": obj,
                "ended": ended,
            }
        )
        return {"ok": True}


@pytest.mark.asyncio
async def test_reconcile_invalidates_older_role_fact():
    facts = {
        "daniel costa": [
            _f("Daniel Costa", "role", "VP of Finance"),
            _f(
                "Daniel Costa",
                "has_role",
                "VP Finance & Administration",
                valid_from="2026-04-24",
            ),
        ]
    }
    client = _FakeClient(facts)
    n = await reconcile.reconcile_entity(
        "Daniel Costa", client
    )
    assert n == 1
    assert len(client.invalidated) == 1
    bad = client.invalidated[0]
    assert bad["predicate"] == "role"
    assert bad["object"] == "VP of Finance"


@pytest.mark.asyncio
async def test_reconcile_keeps_single_fact():
    facts = {
        "alice": [_f("Alice", "role", "PM")]
    }
    client = _FakeClient(facts)
    assert await reconcile.reconcile_entity("Alice", client) == 0
    assert client.invalidated == []


@pytest.mark.asyncio
async def test_reconcile_skips_emails():
    """Emails are intentionally NOT in the reconcile groups."""
    facts = {
        "bob": [
            _f("Bob", "email", "bob@old.com"),
            _f(
                "Bob",
                "email",
                "bob@new.com",
                valid_from="2026-04-01",
            ),
        ]
    }
    client = _FakeClient(facts)
    assert await reconcile.reconcile_entity("Bob", client) == 0
    assert client.invalidated == []


@pytest.mark.asyncio
async def test_reconcile_company_group():
    facts = {
        "carol": [
            _f("Carol", "company", "OldCo"),
            _f(
                "Carol",
                "works_at",
                "NewCo",
                valid_from="2026-03-01",
            ),
        ]
    }
    client = _FakeClient(facts)
    assert await reconcile.reconcile_entity("Carol", client) == 1
    assert client.invalidated[0]["object"] == "OldCo"


@pytest.mark.asyncio
async def test_reconcile_skips_incoming_facts():
    facts = {
        "dave": [
            _f(
                "Eve",
                "is_different_person_from",
                "Dave",
                direction="incoming",
            ),
            _f("Dave", "role", "Engineer"),
        ]
    }
    client = _FakeClient(facts)
    assert await reconcile.reconcile_entity("Dave", client) == 0


@pytest.mark.asyncio
async def test_run_reconcile_iterates_drawers():
    drawers = [
        {
            "id": "d1",
            "text": (
                "Name: Daniel Costa\nRole: VP\n"
            ),
        },
        {
            "id": "d2",
            "text": "Name: Sonia Scibor\n",
        },
    ]
    facts = {
        "daniel costa": [
            _f("Daniel Costa", "role", "Old"),
            _f(
                "Daniel Costa",
                "has_role",
                "New",
                valid_from="2026-04-01",
            ),
        ],
        "sonia scibor": [
            _f("Sonia Scibor", "role", "Solo"),
        ],
    }
    client = _FakeClient(facts, drawers=drawers)
    import yeti.sleep.reconcile as rec

    rec_orig = rec.MemPalaceClient
    rec.MemPalaceClient = lambda: client
    try:
        result = await rec.run_reconcile()
    finally:
        rec.MemPalaceClient = rec_orig

    assert result == {"entities": 2, "invalidated": 1}
