"""Clarification interpreter — turn user answers into KG facts.

When Daniel answers an inbox clarification question, this agent reads
the original question + the source note + Daniel's answer, and produces
KG facts to update YETI's understanding.

It does NOT create tasks, send messages, or call external APIs. It only
updates internal interpretation.
"""

import json
import logging

from yeti import llm
from yeti.config import settings
from yeti.memory.client import MemPalaceClient

logger = logging.getLogger(__name__)

_memory = MemPalaceClient()

CLARIFY_PROMPT = """\
You are YETI's Clarification Interpreter. Daniel just answered a \
clarifying question about a note. Your job is to update YETI's \
understanding based on the answer.

CRITICAL RULES:
- Only produce KG facts that are DIRECTLY supported by the answer.
- Do NOT make up facts.
- Do NOT create tasks, send messages, or trigger external actions.
- You can produce derived facts if they follow logically. \
Example: if the answer says "Anni is Program Manager at Reaktor", \
you can derive: (Anni, role, Program Manager), (Anni, works_at, Reaktor).
- If the answer is "no" or empty, produce no facts.
- If the answer is unclear, produce no facts.

QUESTION:
{question}

CONTEXT (why this question was asked):
{context}

DANIEL'S ANSWER:
{answer}

SOURCE NOTE (the original info the question came from):
{note_excerpt}

Return ONLY a JSON object with this shape:
{{
  "facts": [
    {{"subject": "...", "predicate": "...", "object": "...", \
"valid_from": "YYYY-MM-DD or null"}}
  ],
  "summary": "1 sentence describing what was learned"
}}
"""


async def interpret_answer(
    question: str,
    context: str,
    answer: dict,
    note_excerpt: str = "",
) -> dict:
    """Interpret a user's answer and produce KG facts.

    Returns: {"facts": [...], "summary": "...", "applied": int}
    """
    if not _has_meaningful_answer(answer):
        return {
            "facts": [],
            "summary": "Empty answer, no update",
            "applied": 0,
        }

    answer_text = json.dumps(answer, indent=2)

    prompt = CLARIFY_PROMPT.format(
        question=question,
        context=context or "(no additional context)",
        answer=answer_text,
        note_excerpt=note_excerpt[:2000]
        or "(source note not available)",
    )

    response = await llm.acompletion(
        model=settings.litellm_default_model,
        messages=[{"role": "user", "content": prompt}],
        api_key=settings.anthropic_api_key,
        max_tokens=1024,
        agent="clarify",
        task_type="answer_interpretation",
        request_summary=question[:200],
    )

    raw = response.choices[0].message.content or ""
    parsed = _parse_json(raw)
    if not parsed:
        logger.error(
            "Clarify returned non-JSON: %s", raw[:300]
        )
        return {
            "facts": [],
            "summary": "Could not interpret answer",
            "applied": 0,
        }

    applied = 0
    for fact in parsed.get("facts", []):
        try:
            await _memory.kg_add(
                subject=fact["subject"],
                predicate=fact["predicate"],
                obj=fact["object"],
                valid_from=fact.get("valid_from"),
            )
            applied += 1
        except Exception:
            logger.exception(
                "Failed to apply fact: %s", fact
            )

    return {
        "facts": parsed.get("facts", []),
        "summary": parsed.get("summary", ""),
        "applied": applied,
    }


def _has_meaningful_answer(answer: dict) -> bool:
    """Check if at least one field has a non-empty value."""
    if not answer:
        return False
    for v in answer.values():
        if v and str(v).strip().lower() not in (
            "",
            "none",
            "null",
            "n/a",
        ):
            return True
    return False


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
