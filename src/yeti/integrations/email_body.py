"""Shared HTML-to-text conversion for ingested email bodies.

Outlook (Microsoft Graph) and Gmail both return email bodies as
HTML in the common case. Earlier integration code collapsed all
whitespace and stripped tags inline, producing a single blob with
no paragraph structure and bare ``&gt;`` / ``&nbsp;`` entities
leaking through to the dashboard.

This helper does the minimum needed to keep the result readable:
  * Replace block-level boundaries (``<br>``, ``</p>``, ``</div>``,
    ``</li>``, ``</h[1-6]>``, ``</tr>``, ``</blockquote>``) with a
    real newline before tag-stripping, so paragraphs survive.
  * Strip remaining tags.
  * HTML-unescape entities once.
  * Collapse only horizontal whitespace, preserving newlines.
  * Cap runs of blank lines at two.
"""

from __future__ import annotations

import html as _html
import re

_BLOCK_BOUNDARY_RE = re.compile(
    r"<\s*(?:br\s*/?|/p|/div|/li|/h[1-6]|/tr|/blockquote)\s*>",
    re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")
_HORIZONTAL_WS_RE = re.compile(r"[^\S\n]+")
_BLANKS_RE = re.compile(r"\n{3,}")


def html_to_text(html: str) -> str:
    """Convert an email HTML body to readable plain text."""
    if not html:
        return ""
    s = _BLOCK_BOUNDARY_RE.sub("\n", html)
    s = _TAG_RE.sub(" ", s)
    s = _html.unescape(s)
    s = _HORIZONTAL_WS_RE.sub(" ", s)
    s = _BLANKS_RE.sub("\n\n", s)
    # Strip per-line so leftover space from tag-replacement doesn't
    # show up as leading indentation on every paragraph.
    s = "\n".join(line.strip() for line in s.splitlines())
    return s.strip()
