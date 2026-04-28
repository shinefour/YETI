"""Drain pending PERSON_UPDATE inbox items that are no longer unknown.

Triage creates a "Who is X?" item the moment it sees an unknown name in
a note. If YETI later learns about X via another note, chat, or a
manually-created drawer, the original inbox item still sits there.
This sweep re-runs the same lookup triage uses and auto-resolves
items where the answer is now obvious.
"""

import logging

from yeti.models.inbox import InboxStore, InboxType

logger = logging.getLogger(__name__)


def _extract_name(item) -> str:
    """Pull the original mention out of a PERSON_UPDATE inbox item."""
    name = (item.payload or {}).get("mentioned_as") or ""
    if name:
        return name.strip()
    for field in item.answer_schema or []:
        if (
            isinstance(field, dict)
            and field.get("key") == "full_name"
            and field.get("value")
        ):
            return str(field["value"]).strip()
    return ""


async def _is_now_known(name: str) -> bool:
    """Re-run triage's lookups; True if the name is now resolvable."""
    if not name:
        return False
    from yeti.agents.triage import (
        _find_person_matches,
        _person_known_in_kg,
    )

    try:
        matches = await _find_person_matches(name)
        if matches:
            return True
    except Exception:
        logger.exception("Drawer search failed for %s", name)

    try:
        if await _person_known_in_kg(name):
            return True
    except Exception:
        logger.exception("KG lookup failed for %s", name)

    return False


async def run_stale_persons_sweep() -> dict:
    """Auto-resolve PERSON_UPDATE items whose subject is now known."""
    inbox = InboxStore()
    pending = inbox.list_pending()
    candidates = [
        it
        for it in pending
        if it.type == InboxType.PERSON_UPDATE
    ]

    resolved = 0
    for item in candidates:
        name = _extract_name(item)
        if not name:
            continue
        try:
            known = await _is_now_known(name)
        except Exception:
            logger.exception(
                "Stale-persons check failed for %s", name
            )
            continue
        if not known:
            continue
        try:
            inbox.resolve(
                item.id,
                resolution="auto_resolved",
                note=f"Person '{name}' now known to YETI.",
            )
            resolved += 1
        except Exception:
            logger.exception(
                "Failed to resolve inbox item %s", item.id
            )

    logger.info(
        "Stale-persons sweep: resolved=%d of %d candidates",
        resolved,
        len(candidates),
    )
    return {
        "resolved": resolved,
        "candidates": len(candidates),
    }
