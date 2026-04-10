"""Tests for email filtering and blacklist."""

import pytest

from yeti.email.filters import filter_email
from yeti.models.email_blacklist import EmailBlacklistStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    s = EmailBlacklistStore(db)
    # Patch the global blacklist used by filter_email
    import yeti.email.filters as f

    monkeypatch.setattr(
        f, "EmailBlacklistStore", lambda: s
    )
    return s


def test_blacklist_add_and_match(store):
    store.add("noreply@example.com", "spam")
    assert store.matches("noreply@example.com")
    assert not store.matches("ceo@example.com")


def test_blacklist_wildcard(store):
    store.add("*@spam.com", "blocked domain")
    assert store.matches("anyone@spam.com")
    assert store.matches("noreply@spam.com")
    assert not store.matches("anyone@notspam.com")


def test_blacklist_remove(store):
    store.add("test@x.com")
    assert store.matches("test@x.com")
    store.remove("test@x.com")
    assert not store.matches("test@x.com")


def test_filter_blacklisted(store):
    store.add("blocked@example.com")
    ok, reason = filter_email(
        "blocked@example.com", {}
    )
    assert not ok
    assert "blacklisted" in reason


def test_filter_noisy_sender(store):
    ok, reason = filter_email("noreply@github.com", {})
    assert not ok
    assert "noisy" in reason.lower()


def test_filter_no_reply_variants(store):
    for sender in [
        "no-reply@x.com",
        "do-not-reply@x.com",
        "notifications@github.com",
        "noreply@example.com",
    ]:
        ok, _ = filter_email(sender, {})
        assert not ok, f"{sender} should be filtered"


def test_filter_mailing_list(store):
    ok, reason = filter_email(
        "newsletter@news.com",
        {"List-Unsubscribe": "<mailto:unsub@news.com>"},
    )
    assert not ok
    assert "mailing list" in reason.lower()


def test_filter_auto_submitted(store):
    ok, _ = filter_email(
        "system@x.com",
        {"Auto-Submitted": "auto-generated"},
    )
    assert not ok


def test_filter_passes_normal(store):
    ok, _ = filter_email(
        "joe@conetic.com",
        {"From": "joe@conetic.com"},
    )
    assert ok
