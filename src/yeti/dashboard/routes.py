"""Dashboard routes — HTMX + Jinja2 web interface."""

from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from yeti.agents.chat import chat as chat_agent
from yeti.config import settings
from yeti.models.actions import ActionStatus, ActionStore

router = APIRouter(prefix="/dashboard")

templates = Jinja2Templates(
    directory=str(Path(__file__).parent / "templates")
)


@router.get("/", response_class=HTMLResponse)
async def home_page(request: Request):
    return templates.TemplateResponse(
        request, "home.html", {"active": "chat"}
    )


@router.get("/actions", response_class=HTMLResponse)
async def actions_page(request: Request):
    return templates.TemplateResponse(
        request, "actions.html", {"active": "actions"}
    )


@router.get("/knowledge", response_class=HTMLResponse)
async def knowledge_page(request: Request):
    return templates.TemplateResponse(
        request,
        "placeholder.html",
        {
            "active": "knowledge",
            "page_title": "Knowledge Base",
            "page_description": (
                "Browse project documentation, meeting notes, "
                "and specs. Coming once MemPalace is connected."
            ),
        },
    )


@router.get("/people", response_class=HTMLResponse)
async def people_page(request: Request):
    return templates.TemplateResponse(
        request,
        "placeholder.html",
        {
            "active": "people",
            "page_title": "Person Network",
            "page_description": (
                "Directory of contacts with interaction history "
                "and project links. Coming once memory is seeded."
            ),
        },
    )


@router.get("/activity", response_class=HTMLResponse)
async def activity_page(request: Request):
    return templates.TemplateResponse(
        request,
        "placeholder.html",
        {
            "active": "activity",
            "page_title": "Activity Feed",
            "page_description": (
                "Recent events across integrations and "
                "background agent actions. Coming soon."
            ),
        },
    )


# --- HTMX partials ---


@router.get(
    "/partials/status-sidebar", response_class=HTMLResponse
)
async def status_sidebar_partial():
    from yeti.app import get_system_status

    data = await get_system_status()
    rows = ["<h3>Services</h3>"]
    for name, state in data.get("services", {}).items():
        dot = _dot_for(state)
        rows.append(
            f'<div class="status-item">'
            f'<span class="name">{name}</span>{dot}</div>'
        )
    rows.append("<h3 style='margin-top:0.5rem'>Integrations</h3>")
    for name, state in data.get("integrations", {}).items():
        dot = _dot_for(state)
        rows.append(
            f'<div class="status-item">'
            f'<span class="name">{name}</span>{dot}</div>'
        )
    return "\n".join(rows)


@router.post("/partials/chat", response_class=HTMLResponse)
async def chat_partial(message: str = Form(...)):
    user_html = (
        f'<div class="chat-msg user">'
        f"<strong>Daniel:</strong> {message}</div>"
    )

    if not settings.anthropic_api_key:
        return (
            user_html
            + '<div class="chat-msg assistant">'
            "<strong>YETI:</strong> API key not configured."
            "</div>"
        )

    try:
        response = await chat_agent(message)
        return (
            user_html
            + f'<div class="chat-msg assistant">'
            f"<strong>YETI:</strong> {response}</div>"
        )
    except Exception as e:
        return (
            user_html
            + f'<div class="chat-msg assistant">'
            f"<strong>YETI:</strong> Error: {e}</div>"
        )


@router.get(
    "/partials/actions", response_class=HTMLResponse
)
async def actions_partial(status: str = "pending_review"):
    try:
        action_status = ActionStatus(status)
    except ValueError:
        return "<p class='muted'>Invalid status</p>"

    store = ActionStore()
    items = store.list(status=action_status)

    if not items:
        return "<p class='muted'>None</p>"

    rows = []
    for item in items:
        buttons = ""
        if action_status == ActionStatus.PENDING_REVIEW:
            buttons = (
                f' <button class="badge badge-green" '
                f'hx-patch="/api/actions/{item.id}/status" '
                f'hx-vals=\'{{"status":"active"}}\' '
                f"hx-swap=\"none\" "
                f'hx-on::after-request="'
                f"this.closest('[hx-get]')"
                f'.dispatchEvent(new Event(\'refresh\'))"'
                f">approve</button>"
                f' <button class="badge badge-red" '
                f'hx-patch="/api/actions/{item.id}/status" '
                f'hx-vals=\'{{"status":"cancelled"}}\' '
                f"hx-swap=\"none\" "
                f'hx-on::after-request="'
                f"this.closest('[hx-get]')"
                f'.dispatchEvent(new Event(\'refresh\'))"'
                f">reject</button>"
            )
        elif action_status == ActionStatus.ACTIVE:
            buttons = (
                f' <button class="badge badge-green" '
                f'hx-patch="/api/actions/{item.id}/status" '
                f'hx-vals=\'{{"status":"completed"}}\' '
                f"hx-swap=\"none\" "
                f'hx-on::after-request="'
                f"this.closest('[hx-get]')"
                f'.dispatchEvent(new Event(\'refresh\'))"'
                f">done</button>"
            )

        project_tag = ""
        if item.project:
            project_tag = (
                f' <span class="badge badge-dim">'
                f"{item.project}</span>"
            )

        rows.append(
            f'<div class="status-row">'
            f"<span>{item.title}{project_tag}</span>"
            f"<span>{buttons}</span></div>"
        )
    return "\n".join(rows)


def _dot_for(state: str) -> str:
    if state in ("up", "connected"):
        css = "dot-green"
    elif state == "unknown":
        css = "dot-yellow"
    elif state == "not_configured":
        css = "dot-dim"
    else:
        css = "dot-red"
    return f'<span class="dot {css}"></span>'
