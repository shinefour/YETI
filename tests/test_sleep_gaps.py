"""Tests for the sleep earned-promotions sweep."""

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


def test_pick_canonical_prefers_longer_spelling():
    assert (
        gaps._pick_canonical(["1o1media", "1o1 Media", "1o1media"])
        == "1o1 Media"
    )


def test_pick_canonical_empty():
    assert gaps._pick_canonical([]) == ""
    assert gaps._pick_canonical(["", "  "]) == ""


def test_build_auto_drawer_renders_template():
    text = gaps._build_auto_drawer(
        {
            "email": "max.keil@1o1media.com",
            "name": "Max Keil",
        },
        {
            "role": "CTO",
            "company": "1o1 Media",
            "notes": "- involved_in: American Airlines Project",
        },
    )
    assert text.startswith("# Max Keil")
    assert "Email: max.keil@1o1media.com" in text
    assert "Role: CTO" in text
    assert "Company: 1o1 Media" in text
    assert "American Airlines" in text
    assert "Source: sleep earned-promotion" in text


def test_build_auto_drawer_synthesises_name_from_email():
    text = gaps._build_auto_drawer(
        {"email": "jane.doe@example.com", "name": ""},
        {"role": "PM", "company": "", "notes": ""},
    )
    assert text.startswith("# Jane Doe")


def test_store_succeeded_requires_success_and_id():
    assert gaps._store_succeeded(
        {"success": True, "drawer_id": "drawer_x"}
    )


def test_store_succeeded_rejects_no_drawer_id():
    assert not gaps._store_succeeded({"success": True})


def test_store_succeeded_rejects_explicit_failure():
    assert not gaps._store_succeeded(
        {"success": False, "error": "boom"}
    )


def test_store_succeeded_rejects_non_dict():
    assert not gaps._store_succeeded(None)
    assert not gaps._store_succeeded("ok")
