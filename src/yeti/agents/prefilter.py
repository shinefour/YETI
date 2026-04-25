"""Pre-triage classifier — decide how much processing a note deserves.

Three-tier verdict per note:

- ``discard`` — automated noise (security alerts, calendar ACKs, vendor
  notifications). Mailbox keeps it. YETI does NOT create a drawer or
  extract anything. Embedding space stays clean.
- ``informational`` — FYI content (newsletters, status reports, BCC'd
  threads). Stored as a low-weight drawer in ``room=context-only`` so it
  is searchable later, but no people / facts / inbox items extracted.
- ``full`` — substantive correspondence. Existing triage pipeline runs.

Strategy: cheap deterministic rules first; LLM only for ambiguous cases.
Failures (LLM error / parse error) fail-open to ``full`` so we never lose
content to a classifier hiccup.
"""

import json
import logging
import re

from yeti import llm
from yeti.config import settings
from yeti.models.notes import Note, NoteSource

logger = logging.getLogger(__name__)

# --- Rule layer ---------------------------------------------------------

_NOREPLY_RE = re.compile(
    r"(?:noreply|no-reply|do[_-]?not[_-]?reply|donotreply"
    r"|notifications?|alerts?|mailer-daemon)",
    re.IGNORECASE,
)

_DISCARD_DOMAINS = {
    "accountprotection.microsoft.com",
    "microsoftonline.com",
    "email.microsoft.com",
    "notifications.atlassian.com",
}

_DISCARD_SUBJECT_PATTERNS = [
    re.compile(r"\bsecurity\s+alert\b", re.IGNORECASE),
    re.compile(r"\bunusual sign-?in\b", re.IGNORECASE),
    re.compile(
        r"\b(verify|confirm)(ing|ation)? "
        r"(your )?(email|account|identity)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bone[- ]time (passcode|password|code)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bpassword (reset|changed)\b", re.IGNORECASE),
    re.compile(r"\bmfa code\b", re.IGNORECASE),
    re.compile(
        r"\b(your|the) (invoice|receipt) (is|has|for)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bcalendar (invite|invitation|notification)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(appointment|meeting) (accepted|declined|tentative)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bautomatic reply\b|\bout of office\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(access request received|request for access)\b",
        re.IGNORECASE,
    ),
]

_INFO_SUBJECT_PATTERNS = [
    re.compile(r"\bnewsletter\b", re.IGNORECASE),
    re.compile(
        r"\b(weekly|monthly|daily) (digest|update|summary|report)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bproduct update\b", re.IGNORECASE),
    re.compile(r"\brelease notes\b", re.IGNORECASE),
]


def _email_domain(addr: str) -> str | None:
    if not addr:
        return None
    m = re.search(r"<([^>]+)>", addr)
    if m:
        addr = m.group(1)
    m = re.search(r"([\w.+-]+@[\w.-]+)", addr)
    if m:
        addr = m.group(1)
    if "@" in addr:
        return addr.rsplit("@", 1)[1].lower()
    return None


def _classify_by_rules(
    sender: str, subject: str, headers: dict[str, str]
) -> dict | None:
    """Return verdict dict if a rule matches confidently, else None."""
    auto_submitted = (headers.get("Auto-Submitted") or "").lower()
    if auto_submitted and auto_submitted != "no":
        return {
            "level": "discard",
            "reason": "rule:auto-submitted",
        }

    list_unsub = headers.get("List-Unsubscribe") or ""
    list_id = headers.get("List-Id") or headers.get("List-ID") or ""
    if list_unsub or list_id:
        return {
            "level": "informational",
            "reason": "rule:mailing-list",
        }

    if _NOREPLY_RE.search(sender):
        return {
            "level": "discard",
            "reason": "rule:noreply-sender",
        }

    if _email_domain(sender) in _DISCARD_DOMAINS:
        return {
            "level": "discard",
            "reason": "rule:vendor-notification-domain",
        }

    for pat in _DISCARD_SUBJECT_PATTERNS:
        if pat.search(subject):
            return {
                "level": "discard",
                "reason": f"rule:discard-subject:{pat.pattern[:40]}",
            }

    for pat in _INFO_SUBJECT_PATTERNS:
        if pat.search(subject):
            return {
                "level": "informational",
                "reason": f"rule:info-subject:{pat.pattern[:40]}",
            }

    return None


# --- LLM fallback -------------------------------------------------------

_PROMPT = """\
Classify the email below into ONE of three levels:

- "discard" — automated/notification with no actionable content
  (security alerts, account verification codes, calendar acceptances,
  "you've been added to X", subscription receipts, password resets).
  The mailbox keeps it; YETI doesn't need to remember it.
- "informational" — FYI content worth glancing at (newsletters,
  company announcements, status reports, BCC'd threads). Worth keeping
  a drawer; not worth extracting people / actions / facts.
- "full" — substantive correspondence (decisions, project work, asks,
  replies to Daniel, candidate feedback, real conversation). Run full
  triage to capture people, facts, actions.

Respond with ONLY a JSON object on a single line:
{{"level": "discard|informational|full", "reason": "<short snake_case label>"}}

EMAIL:
From: {sender}
Subject: {subject}
Body (first 800 chars):
{body}
"""


def _parse_email_metadata(
    note: Note,
) -> tuple[dict[str, str], str, str]:
    """Pull headers / sender / subject out of an email-style note."""
    headers: dict[str, str] = {}
    sender = ""
    subject = ""
    body_idx = note.content.find("\n\n")
    head = note.content if body_idx < 0 else note.content[:body_idx]
    for line in head.split("\n"):
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        k = k.strip()
        v = v.strip()
        if not k:
            continue
        headers[k] = v
        kl = k.lower()
        if kl == "from":
            sender = v
        elif kl == "subject":
            subject = v
    return headers, sender, subject


def _parse_classification(raw: str) -> dict:
    text = (raw or "").strip()
    data: dict | None = None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        if "```" in text:
            for block in text.split("```"):
                block = block.strip()
                if block.startswith("json"):
                    block = block[4:].strip()
                try:
                    data = json.loads(block)
                    break
                except json.JSONDecodeError:
                    continue
        if data is None:
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end > start:
                try:
                    data = json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    data = None

    if not isinstance(data, dict):
        return {"level": "full", "reason": "llm-parse-error"}

    level = (data.get("level") or "").lower().strip()
    if level not in {"discard", "informational", "full"}:
        return {
            "level": "full",
            "reason": f"llm-unknown-level:{level or 'empty'}",
        }
    reason = (data.get("reason") or "").strip() or "unspecified"
    return {"level": level, "reason": reason}


async def classify_note_content(note: Note) -> dict:
    """Decide triage level for a note. Always returns {level, reason}."""
    if note.source != NoteSource.EMAIL:
        # Non-email notes (CLI, dashboard, telegram) are user-authored
        # → always full triage.
        return {"level": "full", "reason": "non-email-source"}

    headers, sender, subject = _parse_email_metadata(note)

    rule_verdict = _classify_by_rules(sender, subject, headers)
    if rule_verdict is not None:
        return rule_verdict

    body = note.content[:800]
    try:
        response = await llm.acompletion(
            model=settings.litellm_fast_model,
            messages=[
                {
                    "role": "user",
                    "content": _PROMPT.format(
                        sender=sender or "(unknown)",
                        subject=subject or "(no subject)",
                        body=body,
                    ),
                }
            ],
            api_key=settings.anthropic_api_key,
            max_tokens=128,
            agent="prefilter",
            task_type="note_classify",
            request_summary=(subject or "")[:100],
        )
        raw = response.choices[0].message.content or ""
        return _parse_classification(raw)
    except Exception:
        logger.exception(
            "Prefilter LLM call failed; defaulting to full"
        )
        return {"level": "full", "reason": "llm-error-fail-open"}
