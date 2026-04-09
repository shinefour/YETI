"""Tests for the action items API."""

import os

import pytest
from fastapi.testclient import TestClient

# Use a temp database for tests
os.environ["YETI_DB_PATH"] = ":memory:"

from yeti.app import app
from yeti.models.actions import ActionStore

client = TestClient(app)


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    """Use a fresh database for each test."""
    from yeti.api import actions

    db_path = tmp_path / "test.db"
    actions.store = ActionStore(db_path)
    yield


def test_create_action():
    response = client.post(
        "/api/actions",
        json={"title": "Review PR", "project": "YETI"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "Review PR"
    assert data["status"] == "pending_review"
    assert data["project"] == "YETI"


def test_list_actions_empty():
    response = client.get("/api/actions")
    assert response.status_code == 200
    assert response.json() == []


def test_list_actions_with_filter():
    client.post(
        "/api/actions",
        json={"title": "Task A", "project": "Alpha"},
    )
    client.post(
        "/api/actions",
        json={"title": "Task B", "project": "Beta"},
    )

    response = client.get("/api/actions?project=Alpha")
    items = response.json()
    assert len(items) == 1
    assert items[0]["title"] == "Task A"


def test_get_action():
    create = client.post(
        "/api/actions", json={"title": "Test item"}
    )
    item_id = create.json()["id"]

    response = client.get(f"/api/actions/{item_id}")
    assert response.status_code == 200
    assert response.json()["title"] == "Test item"


def test_get_action_not_found():
    response = client.get("/api/actions/nonexistent")
    assert response.status_code == 404


def test_approve_action():
    create = client.post(
        "/api/actions", json={"title": "Approve me"}
    )
    item_id = create.json()["id"]

    response = client.patch(
        f"/api/actions/{item_id}/status",
        json={"status": "active"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "active"
    assert data["decided_at"] is not None


def test_complete_action():
    create = client.post(
        "/api/actions", json={"title": "Complete me"}
    )
    item_id = create.json()["id"]

    client.patch(
        f"/api/actions/{item_id}/status",
        json={"status": "active"},
    )
    response = client.patch(
        f"/api/actions/{item_id}/status",
        json={"status": "completed"},
    )
    assert response.json()["status"] == "completed"


def test_delete_action():
    create = client.post(
        "/api/actions", json={"title": "Delete me"}
    )
    item_id = create.json()["id"]

    response = client.delete(f"/api/actions/{item_id}")
    assert response.status_code == 200

    response = client.get(f"/api/actions/{item_id}")
    assert response.status_code == 404
