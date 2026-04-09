"""Action items API routes."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from yeti.models.actions import ActionItem, ActionStatus, ActionStore

router = APIRouter(prefix="/api/actions", tags=["actions"])

store = ActionStore()


@router.get("")
async def list_actions(
    status: ActionStatus | None = None,
    project: str | None = None,
):
    items = store.list(status=status, project=project)
    return [item.model_dump() for item in items]


@router.post("", status_code=201)
async def create_action(item: ActionItem):
    created = store.create(item)
    return created.model_dump()


@router.get("/{item_id}")
async def get_action(item_id: str):
    item = store.get(item_id)
    if not item:
        return JSONResponse(
            {"error": "Not found"}, status_code=404
        )
    return item.model_dump()


@router.patch("/{item_id}/status")
async def update_action_status(item_id: str, body: dict):
    new_status = body.get("status")
    if not new_status:
        return JSONResponse(
            {"error": "status field required"}, status_code=400
        )
    try:
        status = ActionStatus(new_status)
    except ValueError:
        return JSONResponse(
            {"error": f"Invalid status: {new_status}"},
            status_code=400,
        )
    item = store.update_status(item_id, status)
    if not item:
        return JSONResponse(
            {"error": "Not found"}, status_code=404
        )
    return item.model_dump()


@router.delete("/{item_id}")
async def delete_action(item_id: str):
    if not store.delete(item_id):
        return JSONResponse(
            {"error": "Not found"}, status_code=404
        )
    return {"deleted": True}
