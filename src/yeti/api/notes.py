"""Notes API — capture raw text for triage."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from yeti.models.notes import Note, NoteStatus, NoteStore

router = APIRouter(prefix="/api/notes", tags=["notes"])

store = NoteStore()


@router.post("", status_code=201)
async def create_note(body: dict):
    content = body.get("content", "").strip()
    if not content:
        return JSONResponse(
            {"error": "content required"}, status_code=400
        )

    note = Note(
        content=content,
        source=body.get("source", "api"),
        title=body.get("title", ""),
        context=body.get("context", ""),
    )
    created = store.create(note)

    # Queue pre-classifier; it dispatches to triage when level=full.
    try:
        from yeti.worker import classify_note

        classify_note.delay(created.id)
    except Exception:
        # If Celery isn't available, just leave as pending
        pass

    return created.model_dump()


@router.get("/recent")
async def recent_notes(limit: int = 50):
    items = store.recent(limit=limit)
    return [item.model_dump() for item in items]


@router.get("/pending")
async def pending_notes():
    items = store.list_by_status(NoteStatus.PENDING)
    return [item.model_dump() for item in items]


@router.get("/{note_id}")
async def get_note(note_id: str):
    note = store.get(note_id)
    if not note:
        return JSONResponse(
            {"error": "Not found"}, status_code=404
        )
    return note.model_dump()
