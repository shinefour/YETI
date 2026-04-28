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


_COMPANY_PREDICATES = ("works_at", "works_for", "employed_at")
_NOTE_PREDICATES = ("involved_in", "shared", "owns", "manages")


def _pick_canonical(values: list[str]) -> str:
    """Pick a canonical spelling from KG fact objects.

    Heuristic: prefer the longest distinct value when multiple
    spellings exist (e.g. "1o1 Media" beats "1o1media"). Returns ""
    if no candidates.
    """
    cleaned = [v.strip() for v in values if v and v.strip()]
    if not cleaned:
        return ""
    seen: dict[str, str] = {}
    for v in cleaned:
        key = re.sub(r"\s+", "", v.lower())
        if key not in seen or len(v) > len(seen[key]):
            seen[key] = v
    return max(seen.values(), key=len)


async def _kg_prefill(name: str, email: str) -> dict:
    """Fetch role / company / notes prefill values from the KG.

    Tries the display name first, then the email local-part. Returns
    a dict with empty strings when nothing is known. Failures are
    swallowed — gap surfacing must still work without KG help.
    """
    from yeti.memory.client import MemPalaceClient

    candidates: list[str] = []
    if name:
        candidates.append(name.strip())
    local = email.split("@", 1)[0] if email else ""
    if local and local not in candidates:
        candidates.append(local)

    facts: list[dict] = []
    client = MemPalaceClient()
    for entity in candidates:
        try:
            res = await client.kg_query(
                entity=entity, source="sleep-gaps"
            )
        except Exception:
            logger.exception("KG query failed for %s", entity)
            continue
        got = res.get("facts") or []
        if got:
            facts = got
            break

    if not facts:
        return {"role": "", "company": "", "notes": ""}

    roles = [
        f.get("object", "")
        for f in facts
        if f.get("direction") == "outgoing"
        and f.get("predicate") == "role"
    ]
    companies = [
        f.get("object", "")
        for f in facts
        if f.get("direction") == "outgoing"
        and f.get("predicate") in _COMPANY_PREDICATES
    ]
    note_lines = [
        f"- {f.get('predicate')}: {f.get('object')}"
        for f in facts
        if f.get("direction") == "outgoing"
        and f.get("predicate") in _NOTE_PREDICATES
        and f.get("object")
    ]

    return {
        "role": _pick_canonical(roles),
        "company": _pick_canonical(companies),
        "notes": "\n".join(note_lines),
    }


def _build_person_update_for_gap(
    gap: dict, prefill: dict | None = None
) -> InboxItem:
    name_value = (gap["name"] or "").strip()
    pf = prefill or {"role": "", "company": "", "notes": ""}
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
                "value": pf.get("role", ""),
            },
            {
                "key": "company",
                "label": "Company",
                "type": "text",
                "value": pf.get("company", ""),
            },
            {
                "key": "context",
                "label": "Notes (optional)",
                "type": "textarea",
                "value": pf.get("notes", ""),
            },
        ],
        quick_actions=["discard"],
        payload={
            "source": "sleep-gaps",
            "email": gap["email"],
            "mentioned_as": name_value or gap["email"],
            "wing_context": "people",
            "kg_prefilled": bool(
                pf.get("role")
                or pf.get("company")
                or pf.get("notes")
            ),
        },
        source="sleep-gaps",
        confidence=0.6,
    )


def _build_auto_drawer(gap: dict, prefill: dict) -> str:
    """Compose drawer text from gap candidate + KG prefill."""
    name = (gap.get("name") or "").strip()
    email = (gap.get("email") or "").strip().lower()
    if not name:
        local = email.split("@", 1)[0] if email else ""
        name = local.replace(".", " ").replace("_", " ").title()

    role = prefill.get("role", "")
    company = prefill.get("company", "")
    notes = prefill.get("notes", "")

    lines = [f"# {name}"]
    if email:
        lines.append(f"Email: {email}")
    if role:
        lines.append(f"Role: {role}")
    if company:
        lines.append(f"Company: {company}")
    if notes:
        lines.append(f"Notes:\n{notes}")
    lines.append("Source: sleep-gaps auto-promotion from KG facts")
    return "\n".join(lines)


async def _auto_promote_drawer(gap: dict, prefill: dict) -> bool:
    """Persist a contact drawer from KG prefill. Returns True on save."""
    from yeti.memory.client import MemPalaceClient

    try:
        client = MemPalaceClient()
        await client.store(
            content=_build_auto_drawer(gap, prefill),
            wing="people",
            room="contacts",
            source="sleep-gaps",
        )
        return True
    except Exception:
        logger.exception(
            "Auto-promote drawer failed for %s", gap.get("email")
        )
        return False


async def run_gap_fill() -> dict:
    """Resolve high-frequency unknowns.

    For each candidate:
      * If KG has enough facts (role or company) -> auto-create a
        contact drawer directly and skip the inbox prompt.
      * Otherwise surface a PERSON_UPDATE inbox item, pre-filled
        with whatever KG fragments exist.
    """
    gaps = find_gap_senders()
    inbox = InboxStore()
    surfaced = 0
    auto_promoted = 0
    for gap in gaps:
        if _has_pending_gap_item(gap["email"], inbox):
            continue
        prefill = await _kg_prefill(gap["name"], gap["email"])
        if prefill.get("role") or prefill.get("company"):
            if await _auto_promote_drawer(gap, prefill):
                auto_promoted += 1
                continue
        try:
            inbox.create(
                _build_person_update_for_gap(gap, prefill)
            )
            surfaced += 1
        except Exception:
            logger.exception(
                "Failed to create gap inbox item for %s",
                gap["email"],
            )
    logger.info(
        "Sleep gap-fill: auto_promoted=%d surfaced=%d "
        "(found=%d above threshold)",
        auto_promoted,
        surfaced,
        len(gaps),
    )
    return {
        "auto_promoted": auto_promoted,
        "surfaced": surfaced,
        "candidates": len(gaps),
    }
