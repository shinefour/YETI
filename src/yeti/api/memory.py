"""Memory API routes — interface to MemPalace."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from yeti.memory.client import MemPalaceClient

router = APIRouter(prefix="/api/memory", tags=["memory"])

_client = MemPalaceClient()


@router.get("/status")
async def memory_status():
    return await _client.status()


@router.get("/wings")
async def list_wings():
    return await _client.list_wings()


@router.get("/rooms")
async def list_rooms(wing: str | None = None):
    return await _client.list_rooms(wing)


@router.post("/search")
async def search_memory(body: dict):
    query = body.get("query", "")
    if not query:
        return JSONResponse(
            {"error": "query required"}, status_code=400
        )
    return await _client.search(
        query=query,
        wing=body.get("wing"),
        room=body.get("room"),
        limit=body.get("limit", 5),
    )


@router.post("/store")
async def store_memory(body: dict):
    content = body.get("content", "")
    wing = body.get("wing", "")
    room = body.get("room", "")
    if not content or not wing or not room:
        return JSONResponse(
            {"error": "content, wing, and room required"},
            status_code=400,
        )
    return await _client.store(
        content=content,
        wing=wing,
        room=room,
        source=body.get("source", "api"),
    )


@router.post("/kg/query")
async def kg_query(body: dict):
    entity = body.get("entity", "")
    if not entity:
        return JSONResponse(
            {"error": "entity required"}, status_code=400
        )
    return await _client.kg_query(
        entity=entity,
        as_of=body.get("as_of"),
    )


@router.post("/kg/add")
async def kg_add(body: dict):
    for field in ("subject", "predicate", "object"):
        if not body.get(field):
            return JSONResponse(
                {"error": f"{field} required"},
                status_code=400,
            )
    return await _client.kg_add(
        subject=body["subject"],
        predicate=body["predicate"],
        obj=body["object"],
        valid_from=body.get("valid_from"),
    )


@router.get("/tools/unimplemented")
async def unimplemented_tools():
    """List MemPalace tools not yet wired into YETI."""
    return _client.get_unimplemented_tools()
