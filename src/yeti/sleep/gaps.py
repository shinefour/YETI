"""Earned-promotion sweep — silently create contact drawers when YETI
already has enough KG knowledge about a high-frequency sender.

Strategy (post-2026-05 redesign): YETI does NOT prompt Daniel with
"Who is X?" inbox items. People bookkeeping is not actionable work —
piling it into the inbox produces noise without resolution pressure.

Instead this module is the autonomous half of the people pipeline:
  * Walk recent email senders.
  * If MemPalace's KG already has role or company for that person,
    silently materialise a contact drawer from those facts.
  * Otherwise do nothing — chat-driven `save_person_profile` is the
    only other path to a profile.

Cold senders (no KG facts) stay shadow-tracked by triage; they will
auto-promote once enough KG facts accumulate over time.
"""

import logging
import re
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from yeti.config import settings

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

_COMPANY_PREDICATES = ("works_at", "works_for", "employed_at")
_NOTE_PREDICATES = ("involved_in", "shared", "owns", "manages")


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


def _has_contact_drawer(email: str, name: str) -> bool:
    """Cheap chromadb-side check for an existing people/contacts drawer."""
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


def _eligible_candidates(
    threshold: int = 3, days: int = 14
) -> list[dict]:
    """Senders past threshold, not blacklisted, without a drawer yet."""
    from yeti.models.email_blacklist import EmailBlacklistStore

    senders = _collect_recent_senders(days=days)
    blacklist = EmailBlacklistStore()
    out: list[dict] = []
    for email, rec in senders.items():
        if rec["count"] < threshold:
            continue
        if blacklist.matches(email):
            continue
        if _has_contact_drawer(email, rec["name"]):
            continue
        out.append(
            {
                "email": email,
                "name": rec["name"],
                "count": rec["count"],
                "last_seen": rec["last_seen"],
            }
        )
    out.sort(key=lambda g: g["count"], reverse=True)
    return out


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
    """Fetch role / company / notes from the KG for a candidate."""
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
                entity=entity, source="sleep-promote"
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


def _build_auto_drawer(gap: dict, prefill: dict) -> str:
    """Compose drawer text from candidate + KG prefill."""
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
    lines.append("Source: sleep earned-promotion from KG facts")
    return "\n".join(lines)


def _store_succeeded(result) -> bool:
    """Treat a MemPalace store response as success only when it says so.

    MemPalace MCP has been observed to return ``success: true`` with a
    drawer_id even when the downstream chromadb upsert failed, and the
    new YETI-side contract is: trust the explicit success flag, log
    anything else so the underlying MCP bug is visible.
    """
    if not isinstance(result, dict):
        logger.warning(
            "Store returned non-dict response: %r", result
        )
        return False
    if result.get("success") is True and result.get("drawer_id"):
        return True
    logger.warning(
        "Store did not confirm success: %s", result
    )
    return False


async def _auto_promote_drawer(gap: dict, prefill: dict) -> bool:
    """Persist a contact drawer from KG prefill. Returns True on confirmed save."""
    from yeti.memory.client import MemPalaceClient

    try:
        client = MemPalaceClient()
        result = await client.store(
            content=_build_auto_drawer(gap, prefill),
            wing="people",
            room="contacts",
            source="sleep-promote",
        )
    except Exception:
        logger.exception(
            "Auto-promote drawer raised for %s", gap.get("email")
        )
        return False
    return _store_succeeded(result)


async def run_earned_promotions() -> dict:
    """Silently materialise contact drawers for KG-known senders.

    No inbox prompts. No People-page "Needs profile" surface. A
    candidate is promoted only when MemPalace's KG already carries
    role or company facts about them. Cold candidates are ignored
    (they will accumulate KG facts via normal triage and become
    eligible on a later night).
    """
    candidates = _eligible_candidates()
    promoted = 0
    skipped_cold = 0
    failed = 0
    for gap in candidates:
        prefill = await _kg_prefill(gap["name"], gap["email"])
        if not (prefill.get("role") or prefill.get("company")):
            skipped_cold += 1
            continue
        if await _auto_promote_drawer(gap, prefill):
            promoted += 1
        else:
            failed += 1
    logger.info(
        "Sleep earned-promotions: promoted=%d cold=%d failed=%d "
        "(candidates=%d)",
        promoted,
        skipped_cold,
        failed,
        len(candidates),
    )
    return {
        "promoted": promoted,
        "cold": skipped_cold,
        "failed": failed,
        "candidates": len(candidates),
    }
