"""Tests for the shared email-HTML-to-text helper."""

from yeti.integrations.email_body import html_to_text


def test_html_to_text_decodes_entities():
    assert "&gt;" not in html_to_text("<p>a &gt; b</p>")
    assert html_to_text("a &amp; b") == "a & b"
    assert html_to_text("non&nbsp;break").startswith("non")


def test_html_to_text_preserves_paragraph_breaks():
    out = html_to_text("<p>Hello</p><p>Daniel</p>")
    assert "Hello" in out and "Daniel" in out
    # The two paragraphs end up on separate lines.
    assert "Hello\n" in out + "\n"
    lines = [ln for ln in out.splitlines() if ln]
    assert lines == ["Hello", "Daniel"]


def test_html_to_text_handles_br():
    out = html_to_text("Hi<br>there<br/>friend")
    assert out.splitlines() == ["Hi", "there", "friend"]


def test_html_to_text_collapses_horizontal_whitespace():
    out = html_to_text("<p>spaced   out  text</p>")
    assert "spaced out text" in out


def test_html_to_text_strips_outer_blank_lines():
    out = html_to_text("<br><br><p>middle</p><br><br>")
    assert out == "middle"


def test_html_to_text_caps_blank_lines():
    out = html_to_text(
        "<p>one</p><br><br><br><br><br><p>two</p>"
    )
    # No run of 3+ newlines should remain.
    assert "\n\n\n" not in out
    assert "one" in out and "two" in out


def test_html_to_text_empty():
    assert html_to_text("") == ""
    assert html_to_text(None) == ""  # type: ignore[arg-type]


def test_html_to_text_quoted_reply_marker_survives():
    # Source has literal `>` after the strip; we don't try to
    # collapse quote chains in this pass.
    out = html_to_text("<p>reply</p><blockquote>orig</blockquote>")
    assert "reply" in out
    assert "orig" in out
