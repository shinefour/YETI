"""Canonical entity lookup — merged drawer + KG + retrieval stats.

When a chat / dashboard / future-Claude session asks "who is X?", a
single API call should return everything we know in one structured
shape. Avoids the multi-hop dance of hitting search, kg_query, and
the retrieval log separately.
"""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter

from yeti.memory.client import MemPalaceClient
from yeti.memory.usage import UsageStore

router = APIRouter(prefix="/api/memory", tags=["memory"])

_client = MemPalaceClient()
_usage = UsageStore()


def _norm(s: str) -> str:
    return (s or "").strip().lower()


@router.get("/entity/{name:path}")
async def get_entity(name: str):
    """Merged entity view: drawer, current KG facts, retrieval stats."""
    name = (name or "").strip()
    if not name:
        return {"name": "", "found": False}

    kg = await _client.kg_query(entity=name, source="api-entity")
    facts = kg.get("facts") if isinstance(kg, dict) else []
    if not isinstance(facts, list):
        facts = []

    # Try to find the canonical contact drawer.
    drawer = None
    try:
        drawers = await _client.search_drawers_with_ids(
            query=name,
            wing="people",
            room="contacts",
            limit=5,
            source="api-entity",
        )
        norm = _norm(name)
        drawer = next(
            (
                d
                for d in drawers
                if norm in (d.get("text") or "").lower()
            ),
            None,
        )
    except Exception:
        drawer = None

    since_30d = datetime.now(UTC) - timedelta(days=30)
    return {
        "name": name,
        "found": bool(facts) or bool(drawer),
        "drawer": drawer,
        "facts": facts,
        "fact_count": len(facts),
        "hit_count_30d": _usage.entity_hit_count(
            name, since=since_30d
        ),
        "last_retrieved": _usage.last_retrieved_for_entity(
            name
        ),
    }
