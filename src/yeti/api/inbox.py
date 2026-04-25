"""Inbox API routes — fast-resolve items."""

import json
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

    # Image fallback manual save → store in MemPalace
    if (
        item.type == InboxType.NOTIFICATION
        and item.payload.get("image_id")
        and resolution == "manual_save"
        and note
    ):
        await _store_image_review(item, note)

    return item.model_dump()


async def _store_image_review(
    item: InboxItem, note_json: str
) -> None:
    """Store manually-reviewed image data in MemPalace."""
    import json as _json

    from yeti.memory.client import MemPalaceClient

    try:
        data = _json.loads(note_json)
    except _json.JSONDecodeError:
        logger.warning(
            "Image review note was not JSON: %s", note_json[:100]
        )
        return

    if not any(data.values()):
        return

    payload = item.payload
    image_id = payload.get("image_id", "")
    caption = payload.get("caption", "")

    if data.get("vendor") or data.get("total"):
        wing, room = "finance", "receipts"
        data["type"] = "receipt"
    else:
        wing, room = "people", "contacts"
        data["type"] = "business_card"

    content = _json.dumps(data, indent=2)
    if caption:
        content = f"Context: {caption}\n\n{content}"
    content += f"\n\nImage: /api/images/{image_id}"
    content += "\nReview: manual"

    try:
        client = MemPalaceClient()
        await client.store(
            content=content,
            wing=wing,
            room=room,
            source="manual-review",
        )
        logger.info(
            "Manual review stored in %s/%s", wing, room
        )
    except Exception:
        logger.exception("Failed to store manual review")


async def _store_person_drawer(
    item: InboxItem, answer: dict
) -> None:
    """Persist a PERSON_UPDATE answer as a searchable contact drawer.

    Without this, `interpret_answer` writes KG facts only — the next
    triage run searches `people/contacts` drawers and re-asks the
    same "Who is X?" question because the contact never materialized
    there.
    """
    from yeti.memory.client import MemPalaceClient

    full_name = str(answer.get("full_name", "") or "").strip()
    if not full_name:
        return

    payload = item.payload or {}
    mentioned_as = payload.get("mentioned_as", "")
    role = str(answer.get("role", "") or "").strip()
    company = str(answer.get("company", "") or "").strip()
    context = str(answer.get("context", "") or "").strip()

    lines = [f"Name: {full_name}"]
    if mentioned_as and mentioned_as != full_name:
        lines.append(f"Mentioned as: {mentioned_as}")
    if role:
        lines.append(f"Role: {role}")
    if company:
        lines.append(f"Company: {company}")
    if context:
        lines.extend(["", context])
    if item.source_note_id:
        lines.extend(["", f"Learned from note: {item.source_note_id}"])

    content = "\n".join(lines)

    try:
        client = MemPalaceClient()
        await client.store(
            content=content,
            wing="people",
            room="contacts",
            source=f"inbox-answer:{item.id}",
        )
        logger.info(
            "Stored contact drawer for %s (mentioned as %s)",
            full_name,
            mentioned_as or "n/a",
        )
    except Exception:
        logger.exception(
            "Failed to store contact drawer for %s", full_name
        )


async def _store_disambiguation_learning(
    item: InboxItem, chosen_full_name: str
) -> None:
    """Store a KG fact so future triage runs can resolve this name."""
    from yeti.memory.client import MemPalaceClient

    payload = item.payload or {}
    name = payload.get("mentioned_as", "")
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


@router.post("/{item_id}/answer")
async def answer_inbox_item(item_id: str, body: dict):
    """Process a clarification answer and update KG facts."""
    from yeti.agents.clarify import interpret_answer
    from yeti.models.notes import NoteStore

    item = store.get(item_id)
    if not item:
        return JSONResponse(
            {"error": "Not found"}, status_code=404
        )

    answer = body.get("answer", {})

    # Get source note excerpt if available
    note_excerpt = ""
    if item.source_note_id:
        notes = NoteStore()
        note = notes.get(item.source_note_id)
        if note:
            note_excerpt = note.content

    try:
        result = await interpret_answer(
            question=item.title,
            context=item.summary,
            answer=answer,
            note_excerpt=note_excerpt,
        )
    except Exception as e:
        logger.exception("Answer interpretation failed")
        return JSONResponse(
            {"error": str(e)}, status_code=500
        )

    if item.type == InboxType.PERSON_UPDATE:
        await _store_person_drawer(item, answer)

    store.resolve(
        item_id,
        "answered",
        note=json.dumps(
            {
                "answer": answer,
                "facts_applied": result["applied"],
                "summary": result["summary"],
            }
        ),
    )

    return {
        "facts_applied": result["applied"],
        "summary": result["summary"],
    }


@router.post("/{item_id}/convert-to-task")
async def convert_to_task(item_id: str, body: dict):
    """Convert an inbox item into a Task and resolve the inbox item."""
    from yeti.models.tasks import Task, TaskStatus, TaskStore

    item = store.get(item_id)
    if not item:
        return JSONResponse(
            {"error": "Not found"}, status_code=404
        )

    title = body.get("title", item.title)
    project = body.get("project", "")

    task_store = TaskStore()
    task = task_store.create(
        Task(
            title=title,
            source=f"inbox:{item_id}",
            project=project,
            context=item.summary,
            status=TaskStatus.ACTIVE,
        )
    )

    # Resolve the inbox item
    store.resolve(
        item_id, "converted_to_task", note=task.id
    )

    return {"task": task.model_dump()}


@router.post("/{item_id}/approve-task")
async def approve_task(item_id: str, body: dict):
    """Approve a PROPOSED_ACTION inbox item, creating an active task."""
    from yeti.models.tasks import Task, TaskStatus, TaskStore

    item = store.get(item_id)
    if not item:
        return JSONResponse(
            {"error": "Not found"}, status_code=404
        )

    answer = body.get("answer", {})
    title = (answer.get("title") or item.title).strip()
    if not title:
        return JSONResponse(
            {"error": "title required"}, status_code=400
        )

    task_store = TaskStore()
    task = task_store.create(
        Task(
            title=title,
            assignee=answer.get("assignee", "") or "",
            due_date=answer.get("due_date") or None,
            project=answer.get("project", "") or "",
            context=item.summary,
            source=f"inbox:{item_id}",
            status=TaskStatus.ACTIVE,
        )
    )

    store.resolve(
        item_id,
        "approved_as_task",
        note=task.id,
    )

    return {"task": task.model_dump()}


@router.get("/{item_id}/audit")
async def item_audit(item_id: str):
    entries = store.audit_log(item_id=item_id)
    return [e.model_dump() for e in entries]


@router.get("/patterns")
async def list_patterns():
    """List learned resolution patterns + their auto-apply status."""
    from yeti.models.resolution_patterns import (
        ResolutionPatternStore,
    )

    store_p = ResolutionPatternStore()
    with store_p._conn() as conn:
        rows = conn.execute(
            """
            SELECT pattern_key, disposition, count,
                   last_seen, auto_apply
            FROM resolution_patterns
            ORDER BY count DESC, last_seen DESC
            LIMIT 200
            """
        ).fetchall()
    return [
        {
            "pattern_key": r["pattern_key"],
            "disposition": r["disposition"],
            "count": r["count"],
            "last_seen": r["last_seen"],
            "auto_apply": bool(r["auto_apply"]),
        }
        for r in rows
    ]


@router.post("/patterns/auto-apply")
async def toggle_auto_apply(body: dict):
    """Flip the auto_apply flag on a learned pattern.

    Body: {"pattern_key": "<type>::<title>", "auto_apply": bool}
    """
    from yeti.models.resolution_patterns import (
        ResolutionPatternStore,
    )

    pattern_key = body.get("pattern_key", "")
    if not pattern_key:
        return JSONResponse(
            {"error": "pattern_key required"}, status_code=400
        )
    enabled = bool(body.get("auto_apply", False))
    if not ResolutionPatternStore().set_auto_apply(
        pattern_key, enabled
    ):
        return JSONResponse(
            {"error": "Pattern not found"}, status_code=404
        )
    return {"pattern_key": pattern_key, "auto_apply": enabled}


@router.get("/audit/recent")
async def recent_audit(limit: int = 100):
    entries = store.audit_log(limit=limit)
    return [e.model_dump() for e in entries]
