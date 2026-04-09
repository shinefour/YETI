"""Tests for the YETI FastAPI application."""

from fastapi.testclient import TestClient

from yeti.app import app

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"


def test_status():
    response = client.get("/api/status")
    assert response.status_code == 200
    data = response.json()
    assert "services" in data
    assert "integrations" in data


def test_chat_empty_message():
    response = client.post("/api/chat", json={"message": ""})
    assert response.status_code == 400


def test_webhook_receiver():
    response = client.post("/webhooks/jira", json={"event": "test"})
    assert response.status_code == 200
    data = response.json()
    assert data["integration"] == "jira"
