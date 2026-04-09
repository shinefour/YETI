"""Chat Agent — handles interactive conversations from all interfaces."""

import litellm

from yeti.config import settings

# Configure LiteLLM
litellm.set_verbose = False

SYSTEM_PROMPT = """\
You are YETI (Your Everyday Task Intelligence), a personal AI
assistant for Daniel. You help consolidate information from
multiple work tools (Teams, Slack, Jira, Notion, Calendar, Email)
and manage a knowledge base, person network, and action items.

You are direct and concise. You focus on actionable information.

Current capabilities:
- Answer questions conversationally
- (Coming soon) Search the knowledge base and memory
- (Coming soon) Query integrations (Jira, Calendar, Teams, etc.)
- (Coming soon) Create and manage action items
"""


async def chat(message: str, conversation_history: list[dict] | None = None) -> str:
    """Send a message to the Chat Agent and get a response."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    if conversation_history:
        messages.extend(conversation_history)

    messages.append({"role": "user", "content": message})

    response = await litellm.acompletion(
        model=settings.litellm_default_model,
        messages=messages,
        api_key=settings.anthropic_api_key,
        max_tokens=1024,
    )

    return response.choices[0].message.content
