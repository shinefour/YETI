"""Tests for the pre-triage classifier rule layer + parser."""

import pytest

from yeti.agents.prefilter import (
    _classify_by_rules,
    _email_domain,
    _parse_classification,
    _parse_email_metadata,
    classify_note_content,
)
from yeti.models.notes import Note, NoteSource


def test_email_domain_plain():
    assert _email_domain("foo@example.com") == "example.com"


def test_email_domain_brackets():
    assert (
        _email_domain("Daniel <daniel@example.com>")
        == "example.com"
    )


def test_email_domain_empty():
    assert _email_domain("") is None
    assert _email_domain("not-an-email") is None


def test_rule_auto_submitted_discards():
    v = _classify_by_rules(
        sender="someone@example.com",
        subject="Out of office",
        headers={"Auto-Submitted": "auto-replied"},
    )
    assert v is not None
    assert v["level"] == "discard"
    assert "auto-submitted" in v["reason"]


def test_rule_mailing_list_information():
    v = _classify_by_rules(
        sender="news@vendor.com",
        subject="Quarterly recap",
        headers={"List-Unsubscribe": "<mailto:x>"},
    )
    assert v is not None
    assert v["level"] == "informational"
    assert "mailing-list" in v["reason"]


def test_rule_noreply_discards():
    v = _classify_by_rules(
        sender="noreply@stuff.io",
        subject="Update",
        headers={},
    )
    assert v is not None
    assert v["level"] == "discard"
    assert "noreply" in v["reason"]


def test_rule_security_subject_discards():
    v = _classify_by_rules(
        sender="security@touch.aero",
        subject="Security alert: unusual sign-in attempt",
        headers={},
    )
    assert v is not None
    assert v["level"] == "discard"


def test_rule_calendar_invitation_discards():
    v = _classify_by_rules(
        sender="someone@example.com",
        subject="Calendar Invitation: Sync at 2pm",
        headers={},
    )
    assert v is not None
    assert v["level"] == "discard"


def test_rule_real_email_returns_none():
    v = _classify_by_rules(
        sender="Michal Zawada <michal@coneticgroup.com>",
        subject="Re: Voyager governance",
        headers={},
    )
    assert v is None


def test_parse_email_metadata_extracts_fields():
    note = Note(
        content=(
            "From: Alice <alice@example.com>\n"
            "To: bob@example.com\n"
            "Subject: Hello there\n"
            "Date: 2026-04-25\n\n"
            "body content here"
        ),
        source=NoteSource.EMAIL,
    )
    headers, sender, subject = _parse_email_metadata(note)
    assert sender == "Alice <alice@example.com>"
    assert subject == "Hello there"
    assert headers["From"] == "Alice <alice@example.com>"


def test_parse_classification_clean_json():
    out = _parse_classification(
        '{"level": "discard", "reason": "automated"}'
    )
    assert out == {"level": "discard", "reason": "automated"}


def test_parse_classification_fenced():
    out = _parse_classification(
        '```json\n{"level": "full", "reason": "real"}\n```'
    )
    assert out == {"level": "full", "reason": "real"}


def test_parse_classification_invalid_falls_open():
    out = _parse_classification("not json at all")
    assert out["level"] == "full"
    assert out["reason"].startswith("llm-parse-error")


def test_parse_classification_unknown_level_falls_open():
    out = _parse_classification(
        '{"level": "maybe", "reason": "x"}'
    )
    assert out["level"] == "full"
    assert "unknown-level" in out["reason"]


@pytest.mark.asyncio
async def test_classify_non_email_is_full():
    note = Note(content="just my thoughts", source=NoteSource.CLI)
    out = await classify_note_content(note)
    assert out == {"level": "full", "reason": "non-email-source"}
