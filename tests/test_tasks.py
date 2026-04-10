"""Tests for the tasks API."""

import os

import pytest
from fastapi.testclient import TestClient

# Use a temp database for tests
os.environ["YETI_DB_PATH"] = ":memory:"

from yeti.app import app
from yeti.config import settings
from yeti.models.tasks import TaskStore

client = TestClient(app)
_KEY = settings.dashboard_api_key
_H = {"x-api-key": _KEY} if _KEY else {}


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    """Use a fresh database for each test."""
    from yeti.api import tasks

    db_path = tmp_path / "test.db"
    tasks.store = TaskStore(db_path)
    yield


def test_create_task():
    response = client.post(
        "/api/tasks",
        headers=_H,
        json={"title": "Review PR", "project": "YETI"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "Review PR"
    assert data["status"] == "active"
    assert data["project"] == "YETI"


def test_list_tasks_empty():
    response = client.get("/api/tasks", headers=_H)
    assert response.status_code == 200
    assert response.json() == []


def test_list_tasks_with_filter():
    client.post(
        "/api/tasks",
        headers=_H,
        json={"title": "Task A", "project": "Alpha"},
    )
    client.post(
        "/api/tasks",
        headers=_H,
        json={"title": "Task B", "project": "Beta"},
    )

    response = client.get(
        "/api/tasks?project=Alpha", headers=_H
    )
    items = response.json()
    assert len(items) == 1
    assert items[0]["title"] == "Task A"


def test_get_task():
    create = client.post(
        "/api/tasks", headers=_H, json={"title": "Test item"}
    )
    item_id = create.json()["id"]

    response = client.get(f"/api/tasks/{item_id}", headers=_H)
    assert response.status_code == 200
    assert response.json()["title"] == "Test item"


def test_get_task_not_found():
    response = client.get(
        "/api/tasks/nonexistent", headers=_H
    )
    assert response.status_code == 404


def test_approve_task():
    create = client.post(
        "/api/tasks", headers=_H, json={"title": "Approve me"}
    )
    item_id = create.json()["id"]

    response = client.patch(
        f"/api/tasks/{item_id}/status",
        headers=_H,
        json={"status": "active"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "active"
    assert data["decided_at"] is not None


def test_complete_task():
    create = client.post(
        "/api/tasks",
        headers=_H,
        json={"title": "Complete me"},
    )
    item_id = create.json()["id"]

    client.patch(
        f"/api/tasks/{item_id}/status",
        headers=_H,
        json={"status": "active"},
    )
    response = client.patch(
        f"/api/tasks/{item_id}/status",
        headers=_H,
        json={"status": "completed"},
    )
    assert response.json()["status"] == "completed"


def test_delete_task():
    create = client.post(
        "/api/tasks", headers=_H, json={"title": "Delete me"}
    )
    item_id = create.json()["id"]

    response = client.delete(
        f"/api/tasks/{item_id}", headers=_H
    )
    assert response.status_code == 200

    response = client.get(f"/api/tasks/{item_id}", headers=_H)
    assert response.status_code == 404
