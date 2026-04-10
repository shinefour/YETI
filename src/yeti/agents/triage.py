"""Triage Agent — processes notes/emails to extract entities, facts,
action items.

Pipeline:
1. Classify the content type
2. Extract structured data (people, projects, dates, action items)
3. Cross-reference against existing memory (disambiguate)
4. Store the raw content as a drawer
5. Add KG facts for new relationships
6. Create inbox items for things needing review
"""

import json
import logging
from datetime import UTC, datetime

from yeti import llm
from yeti.config import settings
from yeti.memory.client import MemPalaceClient
from yeti.models.inbox import InboxItem, InboxStore, InboxType
from yeti.models.notes import Note
from yeti.models.tasks import Task, TaskStore

logger = logging.getLogger(__name__)

_memory = MemPalaceClient()
_inbox = InboxStore()
_tasks = TaskStore()

TRIAGE_PROMPT = """\
You are YETI's Triage Agent. Daniel just submitted the note below.
Your job is to extract structured data so YETI can store it correctly.

Today's date: {today}

The note may be a meeting note, email, idea, status update, or other.

Search the existing memory before deciding on names — call \
mempalace_search if needed to find existing people or projects.

Return ONLY a JSON object with this shape:
{{
  "type": "meeting_note" | "email" | "idea" | "status" | "other",
  "title": "short title for this note",
  "summary": "1-2 sentence summary",
  "wing": "wing where this should be stored (e.g. conetic, above)",
  "room": "room within the wing (e.g. meetings, decisions)",
  "people_mentioned": ["name1", "name2"],
  "projects_mentioned": ["proj1"],
  "facts": [
    {{"subject": "...", "predicate": "...", "object": "...", \
"valid_from": "YYYY-MM-DD or null"}}
  ],
  "action_items": [
    {{"title": "...", "assignee": "Daniel|other name", \
"due_date": "YYYY-MM-DD or null", "context": "..."}}
  ],
  "review_required": [
    {{"reason": "why this needs review", "summary": "..."}}
  ]
}}

Only include facts and action items you are confident about. \
If anything is ambiguous, list it under review_required instead.

NOTE CONTENT:
---
{content}
---
"""


async def triage_note_content(note: Note) -> str:
    """Run the triage pipeline on a note. Returns a short summary."""
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    prompt = TRIAGE_PROMPT.format(
        today=today, content=note.content
    )

    if note.context:
        prompt += f"\n\nADDITIONAL CONTEXT:\n{note.context}\n"

    response = await llm.acompletion(
        model=settings.litellm_default_model,
        messages=[{"role": "user", "content": prompt}],
        api_key=settings.anthropic_api_key,
        max_tokens=2048,
        agent="triage",
        task_type="note_triage",
        request_summary=note.content[:200],
    )

    raw = response.choices[0].message.content or ""
    parsed = _parse_json(raw)
    if not parsed:
        logger.error("Triage returned non-JSON: %s", raw[:300])
        return "Triage failed to return structured data"

    return await _apply_triage_result(note, parsed)


async def _apply_triage_result(
    note: Note, result: dict
) -> str:
    """Apply the triage output: store drawer, add facts, create items."""
    counts = {"facts": 0, "tasks": 0, "inbox": 0}

    wing = result.get("wing", "general").lower()
    room = result.get("room", "notes").lower()
    title = result.get("title", "Note")
    note_type = result.get("type", "other")

    # 1. Store the raw note as a verbatim drawer
    drawer_content = (
        f"# {title}\n"
        f"Type: {note_type}\n"
        f"Source: {note.source.value}\n"
        f"Captured: {note.created_at}\n\n"
        f"{note.content}"
    )
    if note.context:
        drawer_content += f"\n\nContext: {note.context}"
    try:
        await _memory.store(
            content=drawer_content,
            wing=wing,
            room=room,
            source=f"note:{note.id}",
        )
    except Exception:
        logger.exception("Failed to store drawer")

    # 2. Add KG facts
    for fact in result.get("facts", []):
        try:
            await _memory.kg_add(
                subject=fact["subject"],
                predicate=fact["predicate"],
                obj=fact["object"],
                valid_from=fact.get("valid_from"),
            )
            counts["facts"] += 1
        except Exception:
            logger.exception("Failed to add fact: %s", fact)

    # 3. Create tasks for action items
    for action in result.get("action_items", []):
        try:
            task = Task(
                title=action["title"],
                assignee=action.get("assignee", ""),
                due_date=action.get("due_date"),
                context=action.get("context", ""),
                source=f"note:{note.id}",
            )
            _tasks.create(task)
            counts["tasks"] += 1
        except Exception:
            logger.exception("Failed to create task: %s", action)

    # 4. Create inbox items for things needing review
    for review in result.get("review_required", []):
        try:
            _inbox.create(
                InboxItem(
                    type=InboxType.DECISION,
                    title=review.get("reason", "Review needed"),
                    summary=review.get("summary", ""),
                    payload={"note_id": note.id},
                    source=f"triage:{note.id}",
                    confidence=0.5,
                )
            )
            counts["inbox"] += 1
        except Exception:
            logger.exception(
                "Failed to create inbox item: %s", review
            )

    summary_parts = [
        f"Stored {wing}/{room}",
        f"{counts['facts']} fact(s)",
        f"{counts['tasks']} task(s)",
        f"{counts['inbox']} review(s)",
    ]
    return ", ".join(summary_parts)


def _parse_json(text: str) -> dict | None:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    if "```" in text:
        for block in text.split("```"):
            block = block.strip()
            if block.startswith("json"):
                block = block[4:].strip()
            try:
                return json.loads(block)
            except json.JSONDecodeError:
                continue

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass

    return None
