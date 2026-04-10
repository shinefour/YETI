"""Tests for the inbox API."""

import pytest
from fastapi.testclient import TestClient

from yeti.app import app
from yeti.config import settings
from yeti.models.inbox import InboxStore, InboxType

client = TestClient(app)
_KEY = settings.dashboard_api_key
_H = {"x-api-key": _KEY} if _KEY else {}


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    """Use a fresh database for each test."""
    from yeti.api import inbox

    db_path = tmp_path / "test.db"
    inbox.store = InboxStore(db_path)
    yield


def _make_item(title="Test", item_type="decision"):
    return {
        "type": item_type,
        "title": title,
        "summary": "test summary",
        "payload": {"foo": "bar"},
    }


def test_create_inbox_item():
    response = client.post(
        "/api/inbox", headers=_H, json=_make_item()
    )
    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "Test"
    assert data["status"] == "pending"
    assert data["payload"] == {"foo": "bar"}


def test_list_pending_empty():
    response = client.get("/api/inbox", headers=_H)
    assert response.status_code == 200
    assert response.json() == []


def test_count_pending():
    client.post(
        "/api/inbox", headers=_H, json=_make_item("A")
    )
    client.post(
        "/api/inbox", headers=_H, json=_make_item("B")
    )

    response = client.get("/api/inbox/count", headers=_H)
    assert response.json()["pending"] == 2


def test_resolve_item():
    create = client.post(
        "/api/inbox", headers=_H, json=_make_item("Resolve me")
    )
    item_id = create.json()["id"]

    response = client.post(
        f"/api/inbox/{item_id}/resolve",
        headers=_H,
        json={"resolution": "approved", "note": "looks good"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "resolved"
    assert data["resolution"] == "approved"
    assert data["resolution_note"] == "looks good"
    assert data["resolved_at"] is not None


def test_resolved_item_not_in_pending_list():
    create = client.post(
        "/api/inbox", headers=_H, json=_make_item("X")
    )
    item_id = create.json()["id"]

    client.post(
        f"/api/inbox/{item_id}/resolve",
        headers=_H,
        json={"resolution": "approved"},
    )

    pending = client.get("/api/inbox", headers=_H).json()
    assert len(pending) == 0


def test_audit_log_for_item():
    create = client.post(
        "/api/inbox", headers=_H, json=_make_item("Audited")
    )
    item_id = create.json()["id"]

    client.post(
        f"/api/inbox/{item_id}/resolve",
        headers=_H,
        json={"resolution": "rejected"},
    )

    audit = client.get(
        f"/api/inbox/{item_id}/audit", headers=_H
    ).json()
    actions = [a["action"] for a in audit]
    assert "created" in actions
    assert "resolved" in actions


def test_inbox_types_round_trip():
    for t in InboxType:
        client.post(
            "/api/inbox",
            headers=_H,
            json=_make_item(f"item-{t.value}", item_type=t.value),
        )

    items = client.get("/api/inbox", headers=_H).json()
    assert len(items) == len(InboxType)
