"""YETI MCP server — exposes task + memory operations to remote clients.

Mounted at /mcp on the FastAPI app. Auth: the parent app's
auth_middleware validates x-api-key against YETI_DASHBOARD_API_KEY
before any MCP request reaches this transport.
"""

from __future__ import annotations

import logging
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.auth.providers.in_memory import (
    InMemoryOAuthProvider,
)
from mcp.server.auth.settings import ClientRegistrationOptions

from yeti.config import settings
from yeti.memory.client import MemPalaceClient
from yeti.models.inbox import InboxStore
from yeti.models.notes import NoteStore
from yeti.models.tasks import TaskStatus, TaskStore

logger = logging.getLogger(__name__)


def _build_auth_provider():
    """Build the MCP OAuth provider. claude.ai requires OAuth for remote
    connectors; custom request headers are not supported in its UI, so
    bearer-token-in-URL is not an option either."""
    public = (
        settings.dashboard_public_url
        or "http://localhost:8000"
    ).rstrip("/")
    return InMemoryOAuthProvider(
        base_url=f"{public}/mcp",
        client_registration_options=ClientRegistrationOptions(
            enabled=True
        ),
    )


mcp = FastMCP("yeti", auth=_build_auth_provider())

_tasks = TaskStore()
_memory = MemPalaceClient()
_inbox = InboxStore()
_notes = NoteStore()


def _task_to_dict(task) -> dict[str, Any]:
    return task.model_dump()


@mcp.tool
def yeti_get_task(task_id: str) -> dict[str, Any]:
    """Fetch a single task by id, including its outcome."""
    item = _tasks.get(task_id)
    if not item:
        return {"error": "Not found"}
    return _task_to_dict(item)


@mcp.tool
def yeti_list_tasks(status: str = "active") -> dict[str, Any]:
    """List tasks. status: active | blocked | waiting | completed | cancelled."""
    try:
        s = TaskStatus(status)
    except ValueError:
        return {"error": f"Invalid status: {status}"}
    items = _tasks.list(status=s)
    return {
        "count": len(items),
        "items": [_task_to_dict(i) for i in items],
    }


@mcp.tool
def yeti_update_task_status(
    task_id: str, status: str
) -> dict[str, Any]:
    """Transition a task. status: active | blocked | waiting | completed | cancelled."""
    try:
        s = TaskStatus(status)
    except ValueError:
        return {"error": f"Invalid status: {status}"}
    item = _tasks.update_status(task_id, s)
    if not item:
        return {"error": "Not found"}
    return _task_to_dict(item)


@mcp.tool
def yeti_update_task(
    task_id: str,
    outcome: str | None = None,
    context: str | None = None,
    title: str | None = None,
    project: str | None = None,
    assignee: str | None = None,
    due_date: str | None = None,
    nudge_note: str | None = None,
) -> dict[str, Any]:
    """Partial update of task fields."""
    item = _tasks.update_fields(
        task_id,
        outcome=outcome,
        context=context,
        title=title,
        project=project,
        assignee=assignee,
        due_date=due_date,
        nudge_note=nudge_note,
    )
    if not item:
        return {"error": "Not found"}
    return _task_to_dict(item)


@mcp.tool
async def yeti_list_wings() -> Any:
    """List all wings in MemPalace."""
    return await _memory.list_wings()


@mcp.tool
async def yeti_list_rooms(wing: str | None = None) -> Any:
    """List rooms, optionally filtered by wing."""
    return await _memory.list_rooms(wing)


@mcp.tool
async def yeti_search_memory(
    query: str,
    wing: str | None = None,
    room: str | None = None,
    limit: int = 5,
) -> Any:
    """Semantic search across MemPalace drawers."""
    return await _memory.search(
        query=query,
        wing=wing,
        room=room,
        limit=limit,
        source="mcp",
    )


@mcp.tool
async def yeti_store_memory(
    content: str, wing: str, room: str, source: str = "mcp"
) -> Any:
    """Store a drawer in MemPalace. Caller must pin a single wing per session."""
    return await _memory.store(
        content=content, wing=wing, room=room, source=source
    )


@mcp.tool
async def yeti_kg_query(entity: str) -> Any:
    """Query the knowledge graph for facts about an entity."""
    return await _memory.kg_query(entity=entity, source="mcp")


@mcp.tool
async def yeti_kg_add(
    subject: str,
    predicate: str,
    object: str,
    valid_from: str | None = None,
) -> Any:
    """Assert a knowledge-graph fact."""
    return await _memory.kg_add(
        subject=subject,
        predicate=predicate,
        obj=object,
        valid_from=valid_from,
    )


@mcp.tool
def yeti_get_inbox_for_task(task_id: str) -> dict[str, Any]:
    """If the task came from an inbox item, return the inbox item + originating note."""
    task = _tasks.get(task_id)
    if not task:
        return {"error": "Task not found"}
    src = (task.source or "").strip()
    if not src or not src.startswith("inbox:"):
        return {"task_id": task_id, "source": src, "linked": False}
    inbox_id = src.split(":", 1)[1]
    item = _inbox.get(inbox_id) if inbox_id else None
    if not item:
        return {
            "task_id": task_id,
            "source": src,
            "linked": False,
            "error": "Inbox item not found",
        }
    note_id = (
        getattr(item, "source_note_id", None)
        or (item.payload or {}).get("note_id")
        if hasattr(item, "payload")
        else None
    )
    note = _notes.get(note_id) if note_id else None
    return {
        "task_id": task_id,
        "linked": True,
        "inbox_item": item.model_dump(),
        "note": note.model_dump() if note else None,
    }


def http_app():
    """Return the ASGI app for mounting at /mcp on the parent FastAPI."""
    return mcp.http_app(path="/", stateless_http=True)
