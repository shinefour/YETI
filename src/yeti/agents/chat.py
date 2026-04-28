"""Chat Agent — handles interactive conversations from all interfaces."""

import json
import logging

import litellm

from yeti import llm
from yeti.config import settings
from yeti.memory.client import MemPalaceClient

logger = logging.getLogger(__name__)

# Suppress LiteLLM verbose logging
litellm.set_verbose = False  # type: ignore

_memory = MemPalaceClient()

# Tools available to unimplemented
_UNIMPLEMENTED = _memory.get_unimplemented_tools()
_UNIMPLEMENTED_BLOCK = "\n".join(
    f"- {t['name']}: {t['description']}"
    for t in _UNIMPLEMENTED
)

SYSTEM_PROMPT = f"""\
You are YETI (Your Everyday Task Intelligence), a personal AI
assistant for Daniel. You help consolidate information from
multiple work tools (Teams, Slack, Jira, Notion, Calendar, Email)
and manage a knowledge base, person network, and action items.

You are direct and concise. You focus on actionable information.

You have access to a MemPalace memory system. Use the available
tools to search and store memories when relevant to the
conversation. Memories are organized in Wings (projects, people,
domains) and Rooms (decisions, meetings, architecture, etc.).

You also have tools to inspect Daniel's live work queues:
- inbox_list: the clarification/decision queue — items waiting for
  Daniel to answer or approve. These are NOT committed actions.
- tasks_list: committed work (active/blocked/completed/cancelled).
- notes_list_pending: raw notes captured but not yet triaged.
- status_summary: one-shot counts across inbox, tasks, notes.
Use these when Daniel asks what's in his inbox, what's on his
plate, or for a status overview. Do not resolve or modify inbox
items — Daniel acts on them via buttons in Telegram or the dashboard.

When you identify a need for a memory tool that isn't available
yet, mention it to Daniel. The following tools exist in MemPalace
but aren't wired up in YETI yet:
{_UNIMPLEMENTED_BLOCK}

PERSON LOOKUP DISCIPLINE — apply when Daniel asks "who is <name>",
"do you know <name>", "tell me about <name>", or any similar
identification question:

1. If <name> looks like a first name only (single token), do NOT
   conclude "I don't know" after one lookup. KG entities are
   typically stored under full names (e.g. "Sonia Scibor", not
   "Sonia"), so a bare first name often misses both KG and search.
   Run BOTH of these and combine:
   a. memory_search(query=<name>, limit=10) — drawer-side semantic
      hit. People drawers often surface the full name in their
      content, which you can then feed to memory_kg_query.
   b. memory_kg_query(entity=<name>) — long-shot, but covers cases
      where someone IS stored under the bare name.

2. If <name> is a full name (two+ tokens), call
   memory_kg_query(entity=<full name>) FIRST. If empty, fall back
   to memory_search(query=<full name>).

3. Only respond "I don't have stored information about <name>" after
   BOTH a KG and a drawer search returned nothing relevant. If you
   find the person under a different exact spelling (diacritics,
   surname variant), surface that and confirm with Daniel rather
   than asking him to retype.

4. When you find a likely match via memory_search, run a follow-up
   memory_kg_query on the full name discovered in the drawer to
   pull current facts (role, company, recent updates).

CONTACT MERGE PROTOCOL — apply when Daniel asks to merge,
deduplicate, or consolidate two people ("X and Y are the same
person", "merge X into Y", "Y is the canonical name for X"):

1. Locate BOTH drawers via memory_search_with_ids using each name.
   Capture the drawer IDs. If only one drawer exists, no merge is
   needed — just confirm.

2. Pick a canonical name. Prefer the more complete form (full name
   with surname / diacritics) unless Daniel specifies otherwise.

3. KG side:
   a. memory_kg_query(entity=<duplicate name>) to list its facts.
   b. For each outgoing fact under the duplicate, memory_kg_invalidate
      it, then memory_kg_add the same predicate+object under the
      canonical name (re-attribution).
   c. memory_kg_add(subject=<duplicate>, predicate="canonical_name",
      object=<canonical>) so future name resolution folds variants.

4. Drawer side — REQUIRED, not optional:
   memory_delete_drawer(drawer_id=<duplicate drawer id>). Without
   this the dashboard's People page keeps showing both rows.

5. Confirm to Daniel what you did, naming the canonical and what was
   removed. Don't claim "merged" without step 4.

CONTACT PROFILE PROTOCOL — apply when Daniel asks to add, save,
create, or store a contact profile / person profile / person drawer
("add a profile for X", "save a contact for Y", "create a drawer
for Z"):

1. You MUST call save_person_profile to persist the profile. Do not
   ever claim a profile is "saved", "created", or "added" unless
   that tool returned successfully in the same turn. The chat agent
   has no other path that creates a contact drawer.

2. Gather minimum fields conversationally if missing: full_name and
   email. role / company / notes are optional but improve the entry.

3. After the tool returns, summarise what was saved (drawer wing /
   room, plus any KG facts added) — quoting fields back to Daniel.
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "memory_search",
            "description": (
                "Search memories in MemPalace. "
                "Use this to find past decisions, meeting notes, "
                "person context, or any stored knowledge."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to search for",
                    },
                    "wing": {
                        "type": "string",
                        "description": "Filter by wing (project/person)",
                    },
                    "room": {
                        "type": "string",
                        "description": "Filter by room (decisions, meetings...)",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_store",
            "description": (
                "Store important information in MemPalace. "
                "Use this for decisions, meeting notes, "
                "person context, or anything worth remembering."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Content to store verbatim",
                    },
                    "wing": {
                        "type": "string",
                        "description": "Wing (project name, person, or domain)",
                    },
                    "room": {
                        "type": "string",
                        "description": "Room (decisions, meetings, architecture...)",
                    },
                },
                "required": ["content", "wing", "room"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_kg_query",
            "description": (
                "Query the knowledge graph for relationships "
                "about a person, project, or concept."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity": {
                        "type": "string",
                        "description": "Entity to query",
                    },
                },
                "required": ["entity"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_kg_add",
            "description": (
                "Add a fact to the knowledge graph. "
                "Use for relationships like 'Alice owns auth service' "
                "or 'Project Alpha aligned with Q2 goals'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "subject": {
                        "type": "string",
                        "description": "Entity doing/being something",
                    },
                    "predicate": {
                        "type": "string",
                        "description": "Relationship type",
                    },
                    "object": {
                        "type": "string",
                        "description": "Connected entity",
                    },
                    "valid_from": {
                        "type": "string",
                        "description": "When this became true (YYYY-MM-DD)",
                    },
                },
                "required": ["subject", "predicate", "object"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_kg_invalidate",
            "description": (
                "Mark a fact as no longer true. Use when a "
                "previously stored relationship has changed or "
                "was incorrect."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string"},
                    "predicate": {"type": "string"},
                    "object": {"type": "string"},
                    "ended": {
                        "type": "string",
                        "description": "When it stopped being true (YYYY-MM-DD)",
                    },
                },
                "required": ["subject", "predicate", "object"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_kg_timeline",
            "description": (
                "Get a chronological timeline of facts. "
                "Optionally filtered by entity. Useful for "
                "finding recently added or invalid facts."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity": {
                        "type": "string",
                        "description": "Entity to filter by (optional)",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_get_taxonomy",
            "description": (
                "Get the full palace taxonomy: wings, rooms, "
                "and drawer counts. Useful for understanding "
                "what's stored where."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_delete_drawer",
            "description": (
                "Delete a drawer by its ID. Irreversible. Use "
                "to remove misrouted or duplicate content."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "drawer_id": {
                        "type": "string",
                        "description": "Drawer ID to delete",
                    },
                },
                "required": ["drawer_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_search_with_ids",
            "description": (
                "Search drawers and return results WITH their "
                "drawer IDs. Use this when you need to find a "
                "specific drawer to delete or move. Returns id, "
                "text preview, wing, room for each match."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to search for",
                    },
                    "wing": {
                        "type": "string",
                        "description": "Filter by wing",
                    },
                    "room": {
                        "type": "string",
                        "description": "Filter by room",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inbox_list",
            "description": (
                "List pending inbox items (the clarification / "
                "decision queue). Use when Daniel asks what's in "
                "his inbox, what's waiting, or wants to review "
                "pending questions. Read-only — does not resolve."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tasks_list",
            "description": (
                "List tasks (committed work). Optional status "
                "filter: active (default), blocked, completed, "
                "cancelled."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": [
                            "active",
                            "blocked",
                            "completed",
                            "cancelled",
                        ],
                        "description": (
                            "Task status filter (default: active)"
                        ),
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "notes_list_pending",
            "description": (
                "List raw notes captured but not yet processed by "
                "the triage agent. Use when Daniel asks what's "
                "still waiting to be processed."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "status_summary",
            "description": (
                "One-shot counts across inbox, tasks, and notes. "
                "Use for 'status' or 'overview' requests before "
                "drilling in with the list tools."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_person_profile",
            "description": (
                "Persist a contact profile drawer in MemPalace "
                "(wing=people, room=contacts) and add matching KG "
                "facts. REQUIRED whenever Daniel asks to add / save "
                "/ create a profile or contact for a person. Never "
                "claim a profile is saved without calling this tool."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "full_name": {
                        "type": "string",
                        "description": "Person's full name",
                    },
                    "email": {
                        "type": "string",
                        "description": "Primary email address",
                    },
                    "role": {
                        "type": "string",
                        "description": "Role / title (optional)",
                    },
                    "company": {
                        "type": "string",
                        "description": "Company / org (optional)",
                    },
                    "notes": {
                        "type": "string",
                        "description": (
                            "Additional context: relationship, "
                            "projects, preferences (optional)"
                        ),
                    },
                },
                "required": ["full_name", "email"],
            },
        },
    },
]


def _summarize_inbox(item) -> dict:
    return {
        "id": item.id,
        "type": item.type.value,
        "title": item.title,
        "summary": (item.summary or "")[:200],
        "confidence": round(item.confidence, 2),
        "quick_actions": item.quick_actions,
        "has_form": bool(item.answer_schema),
        "created_at": item.created_at,
    }


def _summarize_task(item) -> dict:
    return {
        "id": item.id,
        "title": item.title,
        "status": item.status.value,
        "project": item.project,
        "assignee": item.assignee,
        "due_date": item.due_date,
        "context": (item.context or "")[:200],
        "created_at": item.created_at,
    }


def _summarize_note(item) -> dict:
    return {
        "id": item.id,
        "source": item.source.value,
        "status": item.status.value,
        "preview": (item.content or "")[:200],
        "created_at": item.created_at,
    }


async def _save_person_profile(args: dict) -> dict:
    """Persist a contact profile drawer + KG facts.

    Tool body for save_person_profile. Builds a stable drawer
    template so future merges/searches find consistent text. KG
    enrichment is best-effort — drawer save is the contract.
    """
    full_name = (args.get("full_name") or "").strip()
    email = (args.get("email") or "").strip().lower()
    role = (args.get("role") or "").strip()
    company = (args.get("company") or "").strip()
    notes = (args.get("notes") or "").strip()

    if not full_name or not email:
        return {
            "saved": False,
            "error": "full_name and email are required",
        }

    header = f"# {full_name}"
    line_email = f"Email: {email}"
    line_role = f"Role: {role}" if role else ""
    line_company = f"Company: {company}" if company else ""
    line_notes = f"Notes: {notes}" if notes else ""
    body = "\n".join(
        s
        for s in [
            header,
            line_email,
            line_role,
            line_company,
            line_notes,
        ]
        if s
    )

    store_result = await _memory.store(
        content=body,
        wing="people",
        room="contacts",
        source="chat",
    )

    kg_added: list[dict] = []
    if role:
        try:
            await _memory.kg_add(
                subject=full_name, predicate="role", obj=role
            )
            kg_added.append({"predicate": "role", "object": role})
        except Exception:
            logger.exception(
                "kg_add role failed for %s", full_name
            )
    if company:
        try:
            await _memory.kg_add(
                subject=full_name,
                predicate="works_at",
                obj=company,
            )
            kg_added.append(
                {"predicate": "works_at", "object": company}
            )
        except Exception:
            logger.exception(
                "kg_add works_at failed for %s", full_name
            )

    return {
        "saved": True,
        "wing": "people",
        "room": "contacts",
        "drawer": store_result,
        "kg_facts_added": kg_added,
    }


async def _handle_tool_call(tool_call) -> str:
    """Execute a tool call and return the result as a string."""
    name = tool_call.function.name
    args = json.loads(tool_call.function.arguments)

    if name == "memory_search":
        result = await _memory.search(
            query=args["query"],
            wing=args.get("wing"),
            room=args.get("room"),
            source="chat",
        )
    elif name == "memory_store":
        result = await _memory.store(
            content=args["content"],
            wing=args["wing"],
            room=args["room"],
        )
    elif name == "memory_kg_query":
        result = await _memory.kg_query(
            entity=args["entity"], source="chat"
        )
    elif name == "memory_kg_add":
        result = await _memory.kg_add(
            subject=args["subject"],
            predicate=args["predicate"],
            obj=args["object"],
            valid_from=args.get("valid_from"),
        )
    elif name == "memory_kg_invalidate":
        result = await _memory.kg_invalidate(
            subject=args["subject"],
            predicate=args["predicate"],
            obj=args["object"],
            ended=args.get("ended"),
        )
    elif name == "memory_kg_timeline":
        result = await _memory.kg_timeline(
            entity=args.get("entity")
        )
    elif name == "memory_get_taxonomy":
        result = await _memory.get_taxonomy()
    elif name == "memory_delete_drawer":
        result = await _memory.delete_drawer(
            drawer_id=args["drawer_id"]
        )
    elif name == "memory_search_with_ids":
        result = await _memory.search_drawers_with_ids(
            query=args["query"],
            wing=args.get("wing"),
            room=args.get("room"),
            source="chat",
        )
    elif name == "inbox_list":
        from yeti.models.inbox import InboxStore

        items = InboxStore().list_pending()[:20]
        result = {
            "count": len(items),
            "items": [_summarize_inbox(i) for i in items],
        }
    elif name == "tasks_list":
        from yeti.models.tasks import TaskStatus, TaskStore

        status_arg = args.get("status", "active")
        try:
            status = TaskStatus(status_arg)
        except ValueError:
            result = {
                "error": (
                    f"Unknown status '{status_arg}'. "
                    "Use active, blocked, completed, or cancelled."
                )
            }
        else:
            items = TaskStore().list(status=status)[:20]
            result = {
                "status": status.value,
                "count": len(items),
                "items": [_summarize_task(i) for i in items],
            }
    elif name == "notes_list_pending":
        from yeti.models.notes import NoteStatus, NoteStore

        items = NoteStore().list_by_status(
            NoteStatus.PENDING, limit=20
        )
        result = {
            "count": len(items),
            "items": [_summarize_note(i) for i in items],
        }
    elif name == "status_summary":
        from yeti.models.inbox import InboxStore
        from yeti.models.notes import NoteStatus, NoteStore
        from yeti.models.tasks import TaskStatus, TaskStore

        task_store = TaskStore()
        result = {
            "inbox_pending": InboxStore().count_pending(),
            "tasks_active": len(
                task_store.list(status=TaskStatus.ACTIVE)
            ),
            "tasks_blocked": len(
                task_store.list(status=TaskStatus.BLOCKED)
            ),
            "notes_pending": len(
                NoteStore().list_by_status(
                    NoteStatus.PENDING, limit=1000
                )
            ),
        }
    elif name == "save_person_profile":
        result = await _save_person_profile(args)
    else:
        result = {"error": f"Unknown tool: {name}"}

    return json.dumps(result)


async def chat(
    message: str, conversation_history: list[dict] | None = None
) -> str:
    """Send a message to the Chat Agent and get a response.

    Loops on tool calls until the model returns a final answer
    (max 8 iterations to bound runaway tool chains).
    """
    messages: list = [{"role": "system", "content": SYSTEM_PROMPT}]

    if conversation_history:
        messages.extend(conversation_history)

    messages.append({"role": "user", "content": message})

    max_rounds = 8
    for _ in range(max_rounds):
        response = await llm.acompletion(
            model=settings.litellm_default_model,
            messages=messages,
            tools=TOOLS,
            api_key=settings.anthropic_api_key,
            max_tokens=1024,
            agent="chat",
            task_type="conversation",
            request_summary=message[:200],
        )
        choice = response.choices[0]

        # If no tool calls, we have the final answer
        if (
            choice.finish_reason != "tool_calls"
            or not choice.message.tool_calls
        ):
            return choice.message.content or ""

        # Otherwise, run all tool calls and feed results back
        messages.append(choice.message.model_dump())
        for tool_call in choice.message.tool_calls:
            result = await _handle_tool_call(tool_call)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                }
            )

    # Hit max rounds — return whatever we have
    return (
        choice.message.content
        or "(reached tool call limit, no final answer)"
    )
