"""KG fact reconcile — collapse near-duplicate facts about same entity.

When triage and chat add facts using slightly different predicate names
(``role`` vs ``has_role`` vs ``title``), the KG ends up with multiple
"current" facts that contradict each other for retrieval purposes.

This op groups facts per (subject, predicate-equivalence-group) and,
within each group with more than one current fact, sets ``valid_to`` on
the older facts so only the newest stays current. Same logic for
companies, emails, phones.

Driven per-entity: iterates contact drawers' canonical names. People
with no contact drawer aren't reconciled (yet). Self-healing: as the
auto-rendered drawers grow coverage from feature step 2, this op's
reach grows too.
"""

import logging
from datetime import UTC, datetime, timedelta

from yeti.identity import (
    _COMPANY_PREDICATES,
    _PHONE_PREDICATES,
    _ROLE_PREDICATES,
)
from yeti.memory.client import MemPalaceClient

logger = logging.getLogger(__name__)

_GROUPS: list[set[str]] = [
    _ROLE_PREDICATES,
    _COMPANY_PREDICATES,
    _PHONE_PREDICATES,
    # Note: emails are intentionally NOT auto-reconciled — a person
    # may legitimately have several current emails. Reconcile would
    # invalidate the older one as if superseded, which is wrong.
]


def _predicate_group(pred: str) -> set[str] | None:
    p = (pred or "").lower()
    for grp in _GROUPS:
        if p in grp:
            return grp
    return None


def _newer(a: dict, b: dict) -> dict:
    """Pick newer of two facts using valid_from; ties → b (later in list)."""
    av = a.get("valid_from") or ""
    bv = b.get("valid_from") or ""
    return a if av > bv else b


async def _enumerate_contact_names(
    client: MemPalaceClient,
) -> list[str]:
    """Read names off contact drawers. Best-effort, capped."""
    names: list[str] = []
    try:
        drawers = await client.search_drawers_with_ids(
            query="Name",
            wing="people",
            room="contacts",
            limit=200,
            source="sleep-reconcile",
        )
    except Exception:
        logger.exception("Could not enumerate contact drawers")
        return []

    for d in drawers:
        text = d.get("text") or ""
        for line in text.splitlines():
            line = line.strip()
            if line.lower().startswith("name:"):
                name = line.split(":", 1)[1].strip()
                if name and name not in names:
                    names.append(name)
                break
    return names


async def reconcile_entity(
    name: str, client: MemPalaceClient
) -> int:
    """Run reconcile for one entity. Returns number of facts invalidated."""
    if not name:
        return 0
    try:
        kg = await client.kg_query(
            entity=name, source="sleep-reconcile"
        )
    except Exception:
        logger.exception("KG query failed for %s", name)
        return 0

    facts = kg.get("facts") if isinstance(kg, dict) else []
    if not isinstance(facts, list):
        return 0

    # Only outgoing, current facts about this subject participate.
    current = [
        f
        for f in facts
        if isinstance(f, dict)
        and f.get("direction") != "incoming"
        and f.get("current") is not False
        and (f.get("subject") or "").lower() == name.lower()
    ]

    # Group by predicate-equivalence-group.
    grouped: dict[int, list[dict]] = {}
    for f in current:
        grp = _predicate_group(f.get("predicate") or "")
        if grp is None:
            continue
        key = id(grp)
        grouped.setdefault(key, []).append(f)

    today = datetime.now(UTC).date().isoformat()
    yesterday = (
        datetime.now(UTC).date() - timedelta(days=1)
    ).isoformat()
    invalidated = 0
    for facts_in_group in grouped.values():
        if len(facts_in_group) < 2:
            continue
        # Find the canonical newest.
        canonical = facts_in_group[0]
        for f in facts_in_group[1:]:
            canonical = _newer(canonical, f)

        # Invalidate everything else in the group.
        for f in facts_in_group:
            if f is canonical:
                continue
            try:
                await client.kg_invalidate(
                    subject=f.get("subject"),
                    predicate=f.get("predicate"),
                    obj=f.get("object"),
                    ended=yesterday,
                )
                invalidated += 1
                logger.info(
                    "Reconcile: invalidated %s %s %s "
                    "(superseded by %s %s %s; ended %s)",
                    f.get("subject"),
                    f.get("predicate"),
                    f.get("object"),
                    canonical.get("subject"),
                    canonical.get("predicate"),
                    canonical.get("object"),
                    yesterday,
                )
            except Exception:
                logger.exception(
                    "kg_invalidate failed for %s %s %s",
                    f.get("subject"),
                    f.get("predicate"),
                    f.get("object"),
                )

    # `today` reserved for future logging ext; keep variable scoped.
    _ = today
    return invalidated


async def run_reconcile() -> dict:
    """Reconcile facts across all known contact entities."""
    client = MemPalaceClient()
    names = await _enumerate_contact_names(client)
    total = 0
    for name in names:
        total += await reconcile_entity(name, client)
    logger.info(
        "Sleep reconcile: entities=%d invalidated=%d",
        len(names),
        total,
    )
    return {"entities": len(names), "invalidated": total}
