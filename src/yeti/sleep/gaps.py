"""Surface gaps — people YETI sees often but knows nothing about.

Walks recent notes, tallies sender email addresses, and creates a
PERSON_UPDATE inbox item for each high-frequency sender that lacks a
contact drawer. Deterministic — no LLM. Daniel resolves the inbox
item via the existing schema-driven form, which materialises both a
contact drawer and KG facts via the existing _store_person_drawer
path.

LLM-composed drafts of profile content can layer on top of this in a
later iteration; for now the inbox prompt itself is the surface.
"""

import logging
import re
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from yeti.config import settings
from yeti.models.inbox import (
    InboxItem,
    InboxStore,
    InboxType,
)

logger = logging.getLogger(__name__)
DB_PATH = Path(settings.db_path)

_FROM_RE = re.compile(
    r"^From:\s*(.+)$", re.IGNORECASE | re.MULTILINE
)
_EMAIL_RE = re.compile(
    r"([\w.+-]+@[\w.-]+\.\w+)"
)
_NAME_BEFORE_BRACKET_RE = re.compile(
    r"^([^<]+)<[^>]+>"
)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _extract_sender(from_header: str) -> tuple[str, str]:
    """Return (display_name, email) from a From: header."""
    name = ""
    email = ""
    m = _NAME_BEFORE_BRACKET_RE.match(from_header.strip())
    if m:
        name = (
            m.group(1).strip().strip('"').strip("'")
        )
    em = _EMAIL_RE.search(from_header)
    if em:
        email = em.group(1).lower()
    return name, email


def _collect_recent_senders(
    days: int = 14,
) -> dict[str, dict]:
    """Tally senders across recent notes.

    Returns dict keyed by email, with name, count, last_seen.
    """
    since = (
        datetime.now(UTC) - timedelta(days=days)
    ).isoformat()
    senders: dict[str, dict] = defaultdict(
        lambda: {"name": "", "count": 0, "last_seen": ""}
    )
    try:
        with _conn() as conn:
            rows = conn.execute(
                """
                SELECT id, content, created_at
                FROM notes
                WHERE created_at >= ?
                  AND source = 'email'
                """,
                (since,),
            ).fetchall()
    except sqlite3.OperationalError:
        return {}

    for row in rows:
        m = _FROM_RE.search(row["content"] or "")
        if not m:
            continue
        name, email = _extract_sender(m.group(1))
        if not email:
            continue
        rec = senders[email]
        rec["count"] += 1
        if name and not rec["name"]:
            rec["name"] = name
        ts = row["created_at"]
        if ts > rec["last_seen"]:
            rec["last_seen"] = ts
    return dict(senders)


def _is_known(email: str, name: str) -> bool:
    """Best-effort check if a person matching email/name already exists.

    Looks at people/contacts drawers via direct chromadb access (read).
    Cheap; if anything fails returns False so we err on surfacing.
    """
    try:
        import chromadb

        from yeti.memory.client import MemPalaceClient
    except Exception:
        return False
    try:
        client = MemPalaceClient()
        col = chromadb.PersistentClient(
            path=client.palace_path
        ).get_collection("mempalace_drawers")
        page = col.get(
            where={"wing": "people"},
            include=["documents"],
        )
        docs = page.get("documents") or []
        email_l = email.lower()
        name_l = (name or "").strip().lower()
        for d in docs:
            text = (d or "").lower()
            if email_l and email_l in text:
                return True
            if name_l and name_l in text:
                return True
        return False
    except Exception:
        return False


def _has_pending_gap_item(
    email: str, store: InboxStore
) -> bool:
    """Avoid creating a fresh gap item every night for the same email."""
    for item in store.list_pending():
        if (
            item.type == InboxType.PERSON_UPDATE
            and item.payload.get("source") == "sleep-gaps"
            and item.payload.get("email") == email
        ):
            return True
    return False


def find_gap_senders(
    threshold: int = 3, days: int = 14
) -> list[dict]:
    """High-mention senders not yet represented in memory."""
    from yeti.models.email_blacklist import EmailBlacklistStore

    senders = _collect_recent_senders(days=days)
    blacklist = EmailBlacklistStore()
    gaps = []
    for email, rec in senders.items():
        if rec["count"] < threshold:
            continue
        if blacklist.matches(email):
            continue
        if _is_known(email, rec["name"]):
            continue
        gaps.append(
            {
                "email": email,
                "name": rec["name"],
                "count": rec["count"],
                "last_seen": rec["last_seen"],
            }
        )
    gaps.sort(key=lambda g: g["count"], reverse=True)
    return gaps


def _build_person_update_for_gap(gap: dict) -> InboxItem:
    name_value = (gap["name"] or "").strip()
    return InboxItem(
        type=InboxType.PERSON_UPDATE,
        title=f"Who is '{name_value or gap['email']}'?",
        summary=(
            f"You've received {gap['count']} emails from "
            f"{gap['email']} in the last 14 days but I don't "
            f"have a profile yet. One quick form fills the gap."
        ),
        answer_schema=[
            {
                "key": "full_name",
                "label": "Full name",
                "type": "text",
                "value": name_value,
            },
            {
                "key": "role",
                "label": "Role / title",
                "type": "text",
            },
            {
                "key": "company",
                "label": "Company",
                "type": "text",
            },
            {
                "key": "context",
                "label": "Notes (optional)",
                "type": "textarea",
            },
        ],
        quick_actions=["discard"],
        payload={
            "source": "sleep-gaps",
            "email": gap["email"],
            "mentioned_as": name_value or gap["email"],
            "wing_context": "people",
        },
        source="sleep-gaps",
        confidence=0.6,
    )


def run_gap_fill() -> dict:
    """Surface high-frequency unknowns as PERSON_UPDATE prompts."""
    gaps = find_gap_senders()
    inbox = InboxStore()
    created = 0
    for gap in gaps:
        if _has_pending_gap_item(gap["email"], inbox):
            continue
        try:
            inbox.create(_build_person_update_for_gap(gap))
            created += 1
        except Exception:
            logger.exception(
                "Failed to create gap inbox item for %s",
                gap["email"],
            )
    logger.info(
        "Sleep gap-fill: surfaced=%d "
        "(found=%d above threshold)",
        created,
        len(gaps),
    )
    return {"surfaced": created, "candidates": len(gaps)}
