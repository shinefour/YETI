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

logger = logging.getLogger(__name__)

_memory = MemPalaceClient()
_inbox = InboxStore()

TRIAGE_PROMPT = """\
You are YETI's Triage Agent. Daniel just submitted the note below.
Your job is to extract structured data so YETI can store it correctly,
and to ask Daniel concrete clarifying questions about anything ambiguous.

Today's date: {today}

The note may be a meeting note, email, idea, status update, or other.

ROUTING RULE — IMPORTANT:
If the additional context mentions a "Default wing", you MUST use \
that wing unless the content is unambiguously about a different \
business entity. Cross-organisation routing requires explicit \
evidence in the content (e.g. "this is a Conetic meeting" stated \
plainly), not topical similarity. When in doubt, use the default \
wing.

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
  "clarifications": [
    {{
      "question": "Concrete black-or-white question",
      "context": "Brief context why this matters",
      "schema": [
        {{"key": "field_name", "label": "Label",
          "type": "text|textarea|choice",
          "value": "default value or empty",
          "options": ["only for type=choice"]}}
      ]
    }}
  ]
}}

CRITICAL RULES for clarifications:
- Each clarification must be a CONCRETE, BLACK-OR-WHITE question.
- BAD: "Multiple team references need clarification"
- GOOD: "Is 'Stellar team' the same as 'Stellar Group'?" \
with schema [{{key:"answer", type:"choice", options:["yes","no"]}}]
- BAD: "Verify relationships"
- GOOD: "Who is 'Anni Mononen'?" with schema \
[{{key:"role", type:"text"}}, {{key:"company", type:"text"}}]
- Questions must clarify INTERPRETATION ONLY. Never ask Daniel \
to make a decision that triggers external action.
- Prefer choice fields for binary or short-list answers.
- Use text fields for short open answers (names, dates).
- Use textarea only for genuinely long context.
- If you have a guess, prefill the "value" field so Daniel just confirms.
- If a fact is fully clear, just add it to facts. Only create a \
clarification when you're genuinely uncertain.

Only include facts and action items you are confident about.

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
    counts = {"facts": 0, "inbox": 0}

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

    # 3. Create inbox items for proposed action items
    # (user reviews and approves before they become real tasks)
    for action in result.get("action_items", []):
        try:
            _inbox.create(
                InboxItem(
                    type=InboxType.PROPOSED_ACTION,
                    title=action["title"],
                    summary=action.get("context", "")
                    or "Proposed task from triage",
                    answer_schema=[
                        {
                            "key": "title",
                            "label": "Task title",
                            "type": "text",
                            "value": action["title"],
                        },
                        {
                            "key": "assignee",
                            "label": "Assignee",
                            "type": "text",
                            "value": action.get(
                                "assignee", ""
                            )
                            or "Daniel",
                        },
                        {
                            "key": "due_date",
                            "label": "Due date (YYYY-MM-DD)",
                            "type": "text",
                            "value": action.get("due_date")
                            or "",
                        },
                        {
                            "key": "project",
                            "label": "Project",
                            "type": "text",
                            "value": "",
                        },
                    ],
                    quick_actions=["discard"],
                    source_note_id=note.id,
                    payload={"context": action.get("context", "")},
                    source=f"triage:{note.id}",
                    confidence=0.7,
                )
            )
            counts["inbox"] += 1
        except Exception:
            logger.exception(
                "Failed to create proposed action: %s", action
            )

    # 4. Create inbox items for clarifications (schema-driven)
    for clarification in result.get("clarifications", []):
        try:
            _inbox.create(
                InboxItem(
                    type=InboxType.DECISION,
                    title=clarification.get(
                        "question", "Clarification needed"
                    ),
                    summary=clarification.get("context", ""),
                    answer_schema=clarification.get(
                        "schema", []
                    ),
                    quick_actions=[
                        "discard",
                        "convert_to_task",
                    ],
                    payload={"note_id": note.id},
                    source_note_id=note.id,
                    source=f"triage:{note.id}",
                    confidence=0.5,
                )
            )
            counts["inbox"] += 1
        except Exception:
            logger.exception(
                "Failed to create clarification: %s",
                clarification,
            )

    # 5. Disambiguate people mentioned
    people_mentioned = result.get("people_mentioned", [])
    if people_mentioned:
        disamb_count = await _resolve_people(
            people_mentioned, wing, note
        )
        counts["inbox"] += disamb_count

    summary_parts = [
        f"Stored {wing}/{room}",
        f"{counts['facts']} fact(s)",
        f"{counts['inbox']} inbox item(s)",
    ]
    return ", ".join(summary_parts)


async def _resolve_people(
    names: list[str], wing: str, note: Note
) -> int:
    """For each name, check matches in memory and create inbox items."""
    inbox_created = 0

    for name in names:
        if not name or not name.strip():
            continue

        # Check learned mappings first
        learned = await _check_learned_mapping(name, wing)
        if learned:
            logger.info(
                "Resolved '%s' in %s context to %s (learned)",
                name,
                wing,
                learned,
            )
            continue

        # Search for matches in memory
        matches = await _find_person_matches(name)

        if len(matches) == 0:
            if _inbox.has_pending_for_person(
                InboxType.PERSON_UPDATE, name, wing
            ):
                logger.info(
                    "Skipping duplicate PERSON_UPDATE for '%s' in %s",
                    name,
                    wing,
                )
                continue

            # Unknown person — schema-driven new contact form
            _inbox.create(
                InboxItem(
                    type=InboxType.PERSON_UPDATE,
                    title=f"Who is '{name}'?",
                    summary=(
                        f"First mention of '{name}' in note "
                        f"'{note.title or 'untitled'}'."
                    ),
                    answer_schema=[
                        {
                            "key": "full_name",
                            "label": "Full name",
                            "type": "text",
                            "value": name,
                        },
                        {
                            "key": "role",
                            "label": "Role / title",
                            "type": "text",
                        },
                        {
                            "key": "company",
                            "label": "Company",
                            "type": "text",
                        },
                        {
                            "key": "context",
                            "label": "Notes (optional)",
                            "type": "textarea",
                        },
                    ],
                    quick_actions=["discard"],
                    source_note_id=note.id,
                    payload={
                        "mentioned_as": name,
                        "wing_context": wing,
                    },
                    source=f"triage:{note.id}",
                    confidence=0.7,
                )
            )
            inbox_created += 1

        elif len(matches) == 1:
            # Unique match — already known, no action needed
            logger.info(
                "Resolved '%s' to single match: %s",
                name,
                matches[0].get("title", ""),
            )

        else:
            if _inbox.has_pending_for_person(
                InboxType.DISAMBIGUATION, name, wing
            ):
                logger.info(
                    "Skipping duplicate DISAMBIGUATION for '%s' in %s",
                    name,
                    wing,
                )
                continue

            # Multiple matches — disambiguation as choice schema
            candidate_names = [
                _extract_candidate_name(m) for m in matches
            ]
            _inbox.create(
                InboxItem(
                    type=InboxType.DISAMBIGUATION,
                    title=f"Which '{name}' is this?",
                    summary=(
                        f"In '{note.title or 'note'}' "
                        f"({wing} context). Pick one or type "
                        f"a different name."
                    ),
                    answer_schema=[
                        {
                            "key": "chosen",
                            "label": "Pick the right one",
                            "type": "choice",
                            "options": [
                                *candidate_names,
                                "other (type below)",
                            ],
                        },
                        {
                            "key": "other_name",
                            "label": "Or type a different name",
                            "type": "text",
                        },
                    ],
                    quick_actions=["discard"],
                    source_note_id=note.id,
                    payload={
                        "mentioned_as": name,
                        "wing_context": wing,
                    },
                    source=f"triage:{note.id}",
                    confidence=0.4,
                )
            )
            inbox_created += 1

    return inbox_created


def _extract_candidate_name(match: dict) -> str:
    """Extract a clean candidate name from a memory search match."""
    text = match.get("text", "")
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("Name:"):
            return line.split(":", 1)[1].strip()
    return text[:50] if text else "unknown"


async def _find_person_matches(name: str) -> list[dict]:
    """Search MemPalace for people matching a name."""
    try:
        result = await _memory.search(
            query=name,
            wing="people",
            room="contacts",
            limit=5,
        )
        results = result.get("results", [])
        # Filter to results that actually contain the name token
        name_lower = name.lower()
        filtered = [
            r
            for r in results
            if name_lower in r.get("text", "").lower()
        ]
        return filtered
    except Exception:
        logger.exception("Person search failed for %s", name)
        return []


async def _check_learned_mapping(
    name: str, wing: str
) -> str | None:
    """Check if we've previously learned what '<name>' means in <wing>."""
    try:
        # Stored as: subject="name:Michal", predicate="in_wing:conetic",
        #            object="Michal Zawada"
        result = await _memory.kg_query(
            entity=f"name:{name}"
        )
        # Result format depends on mempalace — check facts
        facts = result.get("facts", [])
        for fact in facts:
            pred = fact.get("predicate", "")
            obj = fact.get("object", "")
            if pred == f"in_wing:{wing}":
                return obj
        return None
    except Exception:
        return None


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
