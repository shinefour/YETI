"""Inbox API routes — fast-resolve items."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from yeti.models.inbox import InboxItem, InboxStore

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
    return item.model_dump()


@router.get("/{item_id}/audit")
async def item_audit(item_id: str):
    entries = store.audit_log(item_id=item_id)
    return [e.model_dump() for e in entries]


@router.get("/audit/recent")
async def recent_audit(limit: int = 100):
    entries = store.audit_log(limit=limit)
    return [e.model_dump() for e in entries]
