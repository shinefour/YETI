"""Tests for sleep gap-fill (sender frequency surfacing)."""

from yeti.sleep import gaps


def test_extract_sender_with_name():
    name, email = gaps._extract_sender(
        'Daniel Mundt <daniel@globalstudio.com>'
    )
    assert name == "Daniel Mundt"
    assert email == "daniel@globalstudio.com"


def test_extract_sender_email_only():
    name, email = gaps._extract_sender("alice@example.com")
    assert name == ""
    assert email == "alice@example.com"


def test_extract_sender_quoted_name():
    name, email = gaps._extract_sender(
        '"Costa, Daniel" <daniel.costa@coneticgroup.com>'
    )
    assert name == "Costa, Daniel"
    assert email == "daniel.costa@coneticgroup.com"


def test_build_person_update_item_carries_metadata():
    item = gaps._build_person_update_for_gap(
        {
            "email": "alice@example.com",
            "name": "Alice Johnson",
            "count": 7,
            "last_seen": "2026-04-25",
        }
    )
    assert item.title.startswith("Who is 'Alice Johnson'")
    assert "7 emails" in item.summary
    assert item.payload["email"] == "alice@example.com"
    assert item.payload["source"] == "sleep-gaps"
    # Schema prefilled with the candidate name
    name_field = next(
        s for s in item.answer_schema if s["key"] == "full_name"
    )
    assert name_field["value"] == "Alice Johnson"


def test_build_person_update_item_no_name_uses_email():
    item = gaps._build_person_update_for_gap(
        {
            "email": "noname@example.com",
            "name": "",
            "count": 4,
            "last_seen": "2026-04-25",
        }
    )
    assert "noname@example.com" in item.title
