"""Tasks API routes."""

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from yeti import llm
from yeti.config import settings
from yeti.models.tasks import Task, TaskStatus, TaskStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

store = TaskStore()


@router.get("")
async def list_tasks(
    status: TaskStatus | None = None,
    project: str | None = None,
):
    items = store.list(status=status, project=project)
    return [item.model_dump() for item in items]


@router.post("", status_code=201)
async def create_task(item: Task):
    created = store.create(item)
    return created.model_dump()


@router.get("/{item_id}")
async def get_task(item_id: str):
    item = store.get(item_id)
    if not item:
        return JSONResponse(
            {"error": "Not found"}, status_code=404
        )
    return item.model_dump()


@router.patch("/{item_id}")
async def update_task(item_id: str, body: dict):
    """Partial update of task fields (outcome, context, title, ...)."""
    item = store.update_fields(item_id, **body)
    if not item:
        return JSONResponse(
            {"error": "Not found"}, status_code=404
        )
    return item.model_dump()


_OUTCOME_DRAFT_PROMPT = (
    "You write one-sentence outcomes for tasks. The outcome describes "
    "what 'done' looks like — a concrete observable result, not the "
    "steps. Keep it under 20 words. No preamble. Output only the "
    "sentence.\n\n"
    "Task title: {title}\n"
    "Context: {context}\n"
)


@router.post("/{item_id}/outcome/draft")
async def draft_outcome(item_id: str):
    """Draft a one-sentence outcome for the task using LLM."""
    item = store.get(item_id)
    if not item:
        return JSONResponse(
            {"error": "Not found"}, status_code=404
        )
    if not settings.anthropic_api_key:
        return JSONResponse(
            {"error": "YETI_ANTHROPIC_API_KEY not configured"},
            status_code=503,
        )
    prompt = _OUTCOME_DRAFT_PROMPT.format(
        title=item.title,
        context=(item.context or "(none)")[:2000],
    )
    try:
        response = await llm.acompletion(
            model=settings.litellm_default_model,
            messages=[{"role": "user", "content": prompt}],
            api_key=settings.anthropic_api_key,
            max_tokens=120,
            agent="task-outcome-draft",
            task_type="outcome_draft",
            request_summary=item.title[:200],
        )
        text = (response.choices[0].message.content or "").strip()
    except Exception:
        logger.exception("outcome draft failed for %s", item_id)
        return JSONResponse(
            {"error": "outcome draft failed"}, status_code=500
        )
    return {"outcome": text}


@router.patch("/{item_id}/status")
async def update_task_status(item_id: str, body: dict):
    new_status = body.get("status")
    if not new_status:
        return JSONResponse(
            {"error": "status field required"}, status_code=400
        )
    try:
        status = TaskStatus(new_status)
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
async def delete_task(item_id: str):
    if not store.delete(item_id):
        return JSONResponse(
            {"error": "Not found"}, status_code=404
        )
    return {"deleted": True}
