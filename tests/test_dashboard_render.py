"""Tests for dashboard rendering helpers."""

from yeti.dashboard.routes import _split_email_headers


def test_split_email_headers_basic():
    content = (
        "From: Daniel Costa <daniel.costa@coneticgroup.com>\n"
        "To: daniel.mundt@coneticgroup.com\n"
        "Subject: Status update\n"
        "Date: 2026-05-10\n"
        "\n"
        "Hi Daniel,\n"
        "Here is the update.\n"
    )
    headers, body = _split_email_headers(content)
    assert ("From", "Daniel Costa <daniel.costa@coneticgroup.com>") in headers
    assert ("Subject", "Status update") in headers
    assert body.startswith("Hi Daniel,")


def test_split_email_headers_preserves_order():
    content = "Subject: hi\nFrom: x@y\n\nbody"
    headers, _ = _split_email_headers(content)
    assert [k for k, _ in headers] == ["Subject", "From"]


def test_split_email_headers_no_headers_returns_full_content():
    content = "Just a free-form note without headers."
    headers, body = _split_email_headers(content)
    assert headers == []
    assert body == content


def test_split_email_headers_stops_at_first_non_header():
    content = (
        "From: a@b\n"
        "Subject: hi\n"
        "Something free-form\n"
        "From: c@d\n"
    )
    headers, body = _split_email_headers(content)
    assert headers == [("From", "a@b"), ("Subject", "hi")]
    assert body.startswith("Something free-form")


def test_split_email_headers_ignores_unknown_keys():
    content = "From: a@b\nX-Spam-Score: 0.1\n\nbody"
    headers, body = _split_email_headers(content)
    assert headers == [("From", "a@b")]
    # X-Spam-Score is not a recognised header → body starts there.
    assert "X-Spam-Score" in body


def test_split_email_headers_empty_content():
    assert _split_email_headers("") == ([], "")
    assert _split_email_headers(None) == ([], "")  # type: ignore[arg-type]
