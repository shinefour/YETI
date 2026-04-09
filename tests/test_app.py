"""Tests for the YETI FastAPI application."""

from fastapi.testclient import TestClient

from yeti.app import app
from yeti.config import settings

_KEY = settings.dashboard_api_key
_H = {"x-api-key": _KEY} if _KEY else {}

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"


def test_root_redirects():
    response = client.get(
        "/", follow_redirects=False, headers=_H
    )
    assert response.status_code == 307
    assert "/dashboard" in response.headers["location"]


def test_status():
    response = client.get("/api/status", headers=_H)
    assert response.status_code == 200
    data = response.json()
    assert "services" in data
    assert "integrations" in data


def test_unauthorized_without_key():
    if not _KEY:
        return
    response = client.get("/api/status")
    assert response.status_code == 401


def test_chat_empty_message():
    response = client.post(
        "/api/chat", json={"message": ""}, headers=_H
    )
    assert response.status_code == 400


def test_chat_no_api_key(monkeypatch):
    from yeti import config

    monkeypatch.setattr(config.settings, "anthropic_api_key", "")
    response = client.post(
        "/api/chat", json={"message": "hello"}, headers=_H
    )
    assert response.status_code == 503
    assert "not configured" in response.json()["error"]


def test_webhook_receiver():
    response = client.post(
        "/webhooks/jira", json={"event": "test"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["integration"] == "jira"


def test_dashboard_home():
    response = client.get("/dashboard", headers=_H)
    assert response.status_code == 200
    assert "YETI" in response.text
    assert "chat" in response.text.lower()


def test_dashboard_status_sidebar():
    response = client.get(
        "/dashboard/partials/status-sidebar", headers=_H
    )
    assert response.status_code == 200
    assert "api" in response.text.lower()
