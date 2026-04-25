"""Identity / contact drawer rendering and materialization.

When a person's facts live only in the KG, both triage and chat
struggle to surface them via drawer search. This module renders a
canonical contact drawer from current KG facts so subsequent
retrievals (triage's drawer search, dashboard queries, future chat
sessions) hit a single rich record per person.

Functions are deterministic — no LLM. Cheap to call after every
new fact about a known entity.
"""

import logging

from yeti.memory.client import MemPalaceClient

logger = logging.getLogger(__name__)

_ROLE_PREDICATES = {
    "role",
    "has_role",
    "title",
    "job_title",
    "position",
}
_COMPANY_PREDICATES = {
    "works_at",
    "company",
    "employer",
    "works_for",
}
_EMAIL_PREDICATES = {"email", "has_email", "email_address"}
_PHONE_PREDICATES = {
    "phone",
    "mobile",
    "has_mobile_number",
    "has_phone",
}


def _first(facts: list[dict], preds: set[str]) -> str | None:
    for f in facts:
        if not isinstance(f, dict):
            continue
        if f.get("direction") == "incoming":
            # "incoming" facts have this entity as object, not subject
            continue
        if (f.get("predicate") or "").lower() in preds:
            obj = f.get("object")
            if obj:
                return str(obj)
    return None


def _all(facts: list[dict], preds: set[str]) -> list[str]:
    seen: list[str] = []
    for f in facts:
        if not isinstance(f, dict):
            continue
        if f.get("direction") == "incoming":
            continue
        if (f.get("predicate") or "").lower() in preds:
            obj = f.get("object")
            if obj and str(obj) not in seen:
                seen.append(str(obj))
    return seen


def render_contact_drawer(name: str, facts: list[dict]) -> str:
    """Render a contact drawer body from current KG facts.

    Drawer always reflects current truth (re-rendered from facts), so
    write_then_read returns coherent state. Emits a `Name:` header
    first so triage's drawer search can find the person via bm25.
    """
    name = name.strip()
    role = _first(facts, _ROLE_PREDICATES)
    company = _first(facts, _COMPANY_PREDICATES)
    emails = _all(facts, _EMAIL_PREDICATES)
    phones = _all(facts, _PHONE_PREDICATES)

    structured_keys = (
        _ROLE_PREDICATES
        | _COMPANY_PREDICATES
        | _EMAIL_PREDICATES
        | _PHONE_PREDICATES
    )
    other = [
        f
        for f in facts
        if isinstance(f, dict)
        and f.get("direction") != "incoming"
        and (f.get("predicate") or "").lower() not in structured_keys
    ]

    lines = [f"Name: {name}"]
    if role:
        lines.append(f"Role: {role}")
    if company:
        lines.append(f"Company: {company}")
    for email in emails:
        lines.append(f"Email: {email}")
    for phone in phones:
        lines.append(f"Phone: {phone}")

    if other:
        lines.append("")
        lines.append("Other facts:")
        for f in other[:12]:
            pred = f.get("predicate", "")
            obj = f.get("object", "")
            lines.append(f"- {pred}: {obj}")

    lines.append("")
    lines.append("(Auto-rendered from knowledge graph.)")
    return "\n".join(lines)


SELF_SOURCE = "self"
_SELF_WING = "people"
_SELF_ROOM = "contacts"


def render_self_drawer(
    full_name: str,
    aliases: list[str] | None,
    emails: list[str] | None,
    notes: str = "",
) -> str:
    """Render the canonical self drawer body."""
    full_name = (full_name or "").strip()
    aliases = [a.strip() for a in (aliases or []) if a and a.strip()]
    emails = [e.strip() for e in (emails or []) if e and e.strip()]

    lines = [f"Name: {full_name}"]
    if aliases:
        lines.append(
            "Aliases / first-name forms: " + ", ".join(aliases)
        )
    if emails:
        for em in emails:
            lines.append(f"Email: {em}")
    lines.append("")
    lines.append("This is the YETI system owner.")
    lines.append(
        "Any reference to these names or emails is a "
        "self-reference — no need to ask 'who is X?'."
    )
    if notes:
        lines.append("")
        lines.append(notes.strip())
    return "\n".join(lines)


async def self_drawer_present(
    client: MemPalaceClient | None = None,
) -> bool:
    """True if a drawer with source='self' exists in people/contacts."""
    client = client or MemPalaceClient()
    try:
        drawers = await client.search_drawers_with_ids(
            query="YETI system owner",
            wing=_SELF_WING,
            room=_SELF_ROOM,
            limit=10,
            source="self-check",
        )
    except Exception:
        logger.exception("self_drawer_present search failed")
        return False
    for d in drawers:
        meta = d.get("metadata") or {}
        added_by = meta.get("added_by") or meta.get("source")
        if added_by == SELF_SOURCE:
            return True
    return False


async def write_self_drawer(
    full_name: str,
    aliases: list[str] | None = None,
    emails: list[str] | None = None,
    notes: str = "",
    client: MemPalaceClient | None = None,
) -> str | None:
    """Write the canonical self drawer (idempotent on source tag)."""
    if not full_name or not full_name.strip():
        return None
    client = client or MemPalaceClient()
    body = render_self_drawer(full_name, aliases, emails, notes)
    try:
        result = await client.store(
            content=body,
            wing=_SELF_WING,
            room=_SELF_ROOM,
            source=SELF_SOURCE,
        )
    except Exception:
        logger.exception("write_self_drawer store failed")
        return None
    if isinstance(result, dict):
        return result.get("drawer_id") or result.get("id")
    return None


async def ensure_contact_drawer(
    name: str,
    client: MemPalaceClient | None = None,
) -> str | None:
    """Render and store a fresh contact drawer for `name`.

    Pulls current KG facts. No-ops if zero facts (nothing to render).
    Returns the new drawer id (or None on failure / no-op).
    """
    if not name or not name.strip():
        return None
    name = name.strip()
    client = client or MemPalaceClient()

    try:
        kg = await client.kg_query(
            entity=name, source="ensure_contact_drawer"
        )
    except Exception:
        logger.exception("KG lookup failed for %s", name)
        return None

    facts = kg.get("facts") if isinstance(kg, dict) else None
    if not isinstance(facts, list) or not facts:
        return None

    body = render_contact_drawer(name, facts)
    try:
        result = await client.store(
            content=body,
            wing="people",
            room="contacts",
            source=f"contact-auto:{name.lower()}",
        )
        if isinstance(result, dict):
            return result.get("drawer_id") or result.get("id")
        return None
    except Exception:
        logger.exception(
            "Failed to store contact drawer for %s", name
        )
        return None
