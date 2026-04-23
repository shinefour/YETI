"""Tests for Outlook / Graph message parsing + helpers."""

from yeti.integrations.outlook import (
    _extract_body,
    _parse_message,
    _slug,
    token_path_for,
)


def test_slug_safe():
    assert _slug("Daniel.Mundt@Above.Aero") == "daniel.mundt_above.aero"
    assert _slug("x y/z") == "x_y_z"


def test_token_path_for(tmp_path):
    p = token_path_for(
        "daniel.mundt@above.aero", base=tmp_path
    )
    assert p.parent == tmp_path
    assert p.name == "daniel.mundt_above.aero.json"


def test_extract_body_text():
    assert (
        _extract_body({"contentType": "text", "content": "hello"})
        == "hello"
    )


def test_extract_body_html_strips():
    out = _extract_body(
        {
            "contentType": "html",
            "content": "<p>Hello  <b>world</b></p>",
        }
    )
    assert out == "Hello world"


def test_extract_body_empty():
    assert _extract_body({}) == ""
    assert _extract_body({"content": ""}) == ""


def test_parse_message_shape():
    raw = {
        "id": "AAMk123",
        "conversationId": "conv-1",
        "subject": "Project update",
        "receivedDateTime": "2026-04-22T10:00:00Z",
        "from": {
            "emailAddress": {
                "name": "Alice",
                "address": "alice@example.com",
            }
        },
        "toRecipients": [
            {
                "emailAddress": {
                    "name": "Daniel",
                    "address": "daniel@example.com",
                }
            }
        ],
        "bodyPreview": "short preview",
        "body": {
            "contentType": "text",
            "content": "Full body text",
        },
        "internetMessageHeaders": [
            {"name": "List-Unsubscribe", "value": "<mailto:x>"},
            {"name": "X-Custom", "value": "foo"},
        ],
    }
    parsed = _parse_message(raw)
    assert parsed["id"] == "AAMk123"
    assert parsed["thread_id"] == "conv-1"
    assert parsed["subject"] == "Project update"
    assert "alice@example.com" in parsed["from"]
    assert parsed["to"] == "daniel@example.com"
    assert parsed["body"] == "Full body text"
    assert parsed["snippet"] == "short preview"
    assert parsed["received_at"] == "2026-04-22T10:00:00Z"
    assert (
        parsed["headers"].get("List-Unsubscribe")
        == "<mailto:x>"
    )


def test_parse_message_handles_missing_fields():
    parsed = _parse_message({"id": "x"})
    assert parsed["id"] == "x"
    assert parsed["from"] == ""
    assert parsed["to"] == ""
    assert parsed["subject"] == ""
    assert parsed["headers"] == {}
