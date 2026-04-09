"""Chat Agent — handles interactive conversations from all interfaces."""

import json
import logging

import litellm

from yeti.config import settings
from yeti.memory.client import MemPalaceClient

logger = logging.getLogger(__name__)

litellm.set_verbose = False

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

When you identify a need for a memory tool that isn't available
yet, mention it to Daniel. The following tools exist in MemPalace
but aren't wired up in YETI yet:
{_UNIMPLEMENTED_BLOCK}
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
]


async def _handle_tool_call(tool_call) -> str:
    """Execute a tool call and return the result as a string."""
    name = tool_call.function.name
    args = json.loads(tool_call.function.arguments)

    if name == "memory_search":
        result = await _memory.search(
            query=args["query"],
            wing=args.get("wing"),
            room=args.get("room"),
        )
    elif name == "memory_store":
        result = await _memory.store(
            content=args["content"],
            wing=args["wing"],
            room=args["room"],
        )
    elif name == "memory_kg_query":
        result = await _memory.kg_query(entity=args["entity"])
    elif name == "memory_kg_add":
        result = await _memory.kg_add(
            subject=args["subject"],
            predicate=args["predicate"],
            obj=args["object"],
            valid_from=args.get("valid_from"),
        )
    else:
        result = {"error": f"Unknown tool: {name}"}

    return json.dumps(result)


async def chat(
    message: str, conversation_history: list[dict] | None = None
) -> str:
    """Send a message to the Chat Agent and get a response."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    if conversation_history:
        messages.extend(conversation_history)

    messages.append({"role": "user", "content": message})

    response = await litellm.acompletion(
        model=settings.litellm_default_model,
        messages=messages,
        tools=TOOLS,
        api_key=settings.anthropic_api_key,
        max_tokens=1024,
    )

    choice = response.choices[0]

    # Handle tool calls
    if choice.finish_reason == "tool_calls" and choice.message.tool_calls:
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

        # Get final response after tool use
        follow_up = await litellm.acompletion(
            model=settings.litellm_default_model,
            messages=messages,
            tools=TOOLS,
            api_key=settings.anthropic_api_key,
            max_tokens=1024,
        )
        return follow_up.choices[0].message.content

    return choice.message.content
