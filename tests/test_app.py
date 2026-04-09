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


def test_chat_no_api_key():
    response = client.post("/api/chat", json={"message": "hello"})
    assert response.status_code == 503
    assert "not configured" in response.json()["error"]


def test_webhook_receiver():
    response = client.post("/webhooks/jira", json={"event": "test"})
    assert response.status_code == 200
    data = response.json()
    assert data["integration"] == "jira"


def test_dashboard_status_page():
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "YETI" in response.text


def test_dashboard_chat_page():
    response = client.get("/dashboard/chat")
    assert response.status_code == 200
    assert "Chat" in response.text


def test_dashboard_services_partial():
    response = client.get("/dashboard/partials/services")
    assert response.status_code == 200
    assert "API" in response.text
