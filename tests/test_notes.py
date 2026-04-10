"""Tests for the notes API."""

import pytest
from fastapi.testclient import TestClient

from yeti.app import app
from yeti.config import settings
from yeti.models.notes import NoteStore

client = TestClient(app)
_KEY = settings.dashboard_api_key
_H = {"x-api-key": _KEY} if _KEY else {}


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    from yeti.api import notes

    db_path = tmp_path / "test.db"
    notes.store = NoteStore(db_path)
    # Avoid actually queuing the celery task
    monkeypatch.setattr(
        "yeti.worker.triage_note.delay",
        lambda _id: None,
    )
    yield


def test_create_note():
    response = client.post(
        "/api/notes",
        headers=_H,
        json={"content": "Test note content"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["content"] == "Test note content"
    assert data["status"] == "pending"


def test_empty_note_rejected():
    response = client.post(
        "/api/notes", headers=_H, json={"content": "   "}
    )
    assert response.status_code == 400


def test_get_note():
    create = client.post(
        "/api/notes",
        headers=_H,
        json={"content": "fetch me"},
    )
    note_id = create.json()["id"]

    response = client.get(
        f"/api/notes/{note_id}", headers=_H
    )
    assert response.status_code == 200
    assert response.json()["content"] == "fetch me"


def test_get_note_not_found():
    response = client.get("/api/notes/missing", headers=_H)
    assert response.status_code == 404


def test_pending_notes():
    for i in range(3):
        client.post(
            "/api/notes",
            headers=_H,
            json={"content": f"note {i}"},
        )

    response = client.get("/api/notes/pending", headers=_H)
    assert len(response.json()) == 3


def test_recent_notes():
    for i in range(5):
        client.post(
            "/api/notes",
            headers=_H,
            json={"content": f"note {i}"},
        )

    response = client.get(
        "/api/notes/recent?limit=3", headers=_H
    )
    assert len(response.json()) == 3


def test_note_with_metadata():
    response = client.post(
        "/api/notes",
        headers=_H,
        json={
            "content": "meeting summary",
            "title": "Architecture sync",
            "context": "with Joe and Michal",
            "source": "telegram",
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "Architecture sync"
    assert data["context"] == "with Joe and Michal"
    assert data["source"] == "telegram"
