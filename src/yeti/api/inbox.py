"""Inbox API routes — fast-resolve items."""

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from yeti.models.inbox import InboxItem, InboxStore, InboxType

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/inbox", tags=["inbox"])

store = InboxStore()


@router.get("")
async def list_inbox():
    items = store.list_pending()
    return [item.model_dump() for item in items]


@router.get("/count")
async def count_inbox():
    return {"pending": store.count_pending()}


@router.post("", status_code=201)
async def create_inbox_item(item: InboxItem):
    created = store.create(item)
    return created.model_dump()


@router.get("/{item_id}")
async def get_inbox_item(item_id: str):
    item = store.get(item_id)
    if not item:
        return JSONResponse(
            {"error": "Not found"}, status_code=404
        )
    return item.model_dump()


@router.post("/{item_id}/resolve")
async def resolve_inbox_item(item_id: str, body: dict):
    resolution = body.get("resolution", "")
    if not resolution:
        return JSONResponse(
            {"error": "resolution required"}, status_code=400
        )
    note = body.get("note", "")
    item = store.resolve(item_id, resolution, note)
    if not item:
        return JSONResponse(
            {"error": "Not found"}, status_code=404
        )

    # If this was a disambiguation, store the learned mapping
    if item.type == InboxType.DISAMBIGUATION and resolution:
        await _store_disambiguation_learning(item, resolution)

    return item.model_dump()


async def _store_disambiguation_learning(
    item: InboxItem, chosen_full_name: str
) -> None:
    """Store a KG fact so future triage runs can resolve this name."""
    from yeti.memory.client import MemPalaceClient

    payload = item.payload or {}
    name = payload.get("name", "")
    wing = payload.get("wing_context", "")
    if not name or not wing:
        return

    try:
        client = MemPalaceClient()
        await client.kg_add(
            subject=f"name:{name}",
            predicate=f"in_wing:{wing}",
            obj=chosen_full_name,
        )
        logger.info(
            "Learned: '%s' in %s context = %s",
            name,
            wing,
            chosen_full_name,
        )
    except Exception:
        logger.exception("Failed to store disambiguation learning")


@router.get("/{item_id}/audit")
async def item_audit(item_id: str):
    entries = store.audit_log(item_id=item_id)
    return [e.model_dump() for e in entries]


@router.get("/audit/recent")
async def recent_audit(limit: int = 100):
    entries = store.audit_log(limit=limit)
    return [e.model_dump() for e in entries]
