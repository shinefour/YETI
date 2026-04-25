"""Dashboard routes — HTMX + Jinja2 web interface."""

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from yeti.agents.chat import chat as chat_agent
from yeti.config import settings
from yeti.models.inbox import InboxStore
from yeti.models.tasks import TaskStatus, TaskStore

router = APIRouter(prefix="/dashboard")

templates = Jinja2Templates(
    directory=str(Path(__file__).parent / "templates")
)


@router.get("/", response_class=HTMLResponse)
async def home_page(request: Request):
    return templates.TemplateResponse(
        request, "home.html", {"active": "home"}
    )


@router.get("/tasks", response_class=HTMLResponse)
async def tasks_page(request: Request):
    return templates.TemplateResponse(
        request, "tasks.html", {"active": "tasks"}
    )


@router.get("/inbox", response_class=HTMLResponse)
async def inbox_page(request: Request):
    return templates.TemplateResponse(
        request, "inbox.html", {"active": "inbox"}
    )


@router.get("/notes", response_class=HTMLResponse)
async def notes_page(request: Request):
    return templates.TemplateResponse(
        request, "notes.html", {"active": "notes"}
    )


@router.get("/people", response_class=HTMLResponse)
async def people_page(request: Request):
    return templates.TemplateResponse(
        request, "people.html", {"active": "people"}
    )


@router.get("/usage", response_class=HTMLResponse)
async def usage_page(request: Request):
    return templates.TemplateResponse(
        request, "usage.html", {"active": "usage"}
    )


# --- HTMX partials ---


@router.get(
    "/partials/status-sidebar", response_class=HTMLResponse
)
async def status_sidebar_partial():
    from yeti.app import get_system_status

    data = await get_system_status()
    divider = (
        "border-top:1px solid var(--border);"
        "padding-top:0.75rem;margin-top:0.75rem"
    )
    rows = ["<h3>Services</h3>"]
    for name, state in data.get("services", {}).items():
        dot = _dot_for(state)
        rows.append(
            f'<div class="status-item">'
            f'<span class="name">{name}</span>{dot}</div>'
        )
    rows.append(
        f'<h3 style="{divider}">Integrations</h3>'
    )
    for name, state in data.get("integrations", {}).items():
        dot = _dot_for(state)
        label = _integration_label(name)
        rows.append(
            f'<div class="status-item" title="{name}">'
            f'<span class="name">{label}</span>{dot}</div>'
        )
    return "\n".join(rows)


def _integration_label(name: str) -> str:
    """Short, readable label for the sidebar.

    Collapses `outlook:<email>` to `outlook · <wing>` when the mailbox
    is configured; otherwise just `outlook · <email local-part>`. Full
    value remains available via the row's title attribute.
    """
    if not name.startswith("outlook:"):
        return name
    from yeti.config import settings

    email = name.split(":", 1)[1]
    wing = settings.outlook_mailbox_map().get(email.lower())
    if wing:
        return f"outlook · {wing}"
    local = email.split("@", 1)[0]
    return f"outlook · {local}"


@router.get("/events")
async def events_stream():
    """SSE stream for live dashboard updates.

    Polls the underlying stores at a low frequency and emits an
    event whenever a relevant counter changes.
    """

    async def event_generator():
        last_state = {}

        while True:
            try:
                inbox = InboxStore()
                tasks = TaskStore()

                from yeti.models.notes import (
                    NoteStatus,
                    NoteStore,
                )

                notes = NoteStore()

                state = {
                    "inbox_pending": inbox.count_pending(),
                    "tasks_active": len(
                        tasks.list(status=TaskStatus.ACTIVE)
                    ),
                    "tasks_blocked": len(
                        tasks.list(status=TaskStatus.BLOCKED)
                    ),
                    "notes_in_flight": (
                        len(
                            notes.list_by_status(
                                NoteStatus.PENDING
                            )
                        )
                        + len(
                            notes.list_by_status(
                                NoteStatus.PROCESSING
                            )
                        )
                    ),
                }

                if state != last_state:
                    yield (
                        "event: update\n"
                        "data: " + json.dumps(state) + "\n\n"
                    )
                    last_state = state
                else:
                    # Heartbeat to keep connection alive
                    yield ": ping\n\n"

                await asyncio.sleep(5)
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    "/partials/usage-sidebar", response_class=HTMLResponse
)
async def usage_sidebar_partial():
    from yeti.api.usage import usage_summary

    data = await usage_summary()
    pct = data["budget_used_pct"]
    if pct >= 100:
        bar_color = "var(--red)"
    elif pct >= data["alert_threshold_pct"]:
        bar_color = "var(--yellow)"
    else:
        bar_color = "var(--green)"

    return f"""
    <h3>Usage</h3>
    <div class="status-item">
      <span class="name">Today</span>
      <span>${data["today_usd"]:.2f}</span>
    </div>
    <div class="status-item">
      <span class="name">Month</span>
      <span>${data["month_paid_usd"]:.2f}</span>
    </div>
    <div style="padding: 0.4rem 0.5rem">
      <div style="background: var(--border); height: 3px;
                  border-radius: 2px; overflow: hidden">
        <div style="background: {bar_color}; height: 100%;
                    width: {min(pct, 100):.1f}%"></div>
      </div>
      <div style="font-size: 0.65rem; color: var(--text-dim);
                  margin-top: 0.2rem; text-align: right">
        {pct:.0f}% of ${data["budget_usd"]:.0f}
      </div>
    </div>
    """


@router.get(
    "/partials/home-tiles", response_class=HTMLResponse
)
async def home_tiles_partial():
    """Render the control center tiles with latest data."""
    from datetime import UTC, datetime

    inbox = InboxStore()
    pending_inbox = inbox.list_pending()
    inbox_count = len(pending_inbox)
    inbox_active_title = (
        pending_inbox[0].title if pending_inbox else "All clear"
    )
    inbox_color = (
        "var(--yellow)" if inbox_count > 0 else "var(--green)"
    )

    tasks = TaskStore()
    active_tasks = tasks.list(status=TaskStatus.ACTIVE)
    blocked_tasks = tasks.list(status=TaskStatus.BLOCKED)
    active_count = len(active_tasks)
    blocked_count = len(blocked_tasks)

    from yeti.models.notes import NoteStatus, NoteStore

    notes = NoteStore()
    pending_notes = notes.list_by_status(
        NoteStatus.PENDING
    )
    processing_notes = notes.list_by_status(
        NoteStatus.PROCESSING
    )
    notes_in_flight = len(pending_notes) + len(
        processing_notes
    )

    now = datetime.now(UTC).strftime("%H:%M:%S UTC")

    return f"""
    <div class="tile" onclick="location.href='/dashboard/inbox'">
      <div class="tile-label">Inbox</div>
      <div class="tile-value" style="color: {inbox_color}">
        {inbox_count}
      </div>
      <div class="tile-detail">{inbox_active_title}</div>
      <div class="tile-meta">
        <span><span class="pulse-dot"></span>updated {now}</span>
        <span>tap to open →</span>
      </div>
    </div>

    <div class="tile tile-action" onclick="openNoteModal()">
      <div class="tile-label">Capture</div>
      <div class="tile-value">+ Note</div>
      <div class="tile-detail">
        Quick capture for triage
      </div>
      <div class="tile-meta">
        <span>{notes_in_flight} in flight</span>
        <span>tap to add →</span>
      </div>
    </div>

    <div class="tile" onclick="location.href='/dashboard/tasks'">
      <div class="tile-label">Tasks</div>
      <div class="tile-value">{active_count}</div>
      <div class="tile-detail">
        {active_count} active · {blocked_count} blocked
      </div>
      <div class="tile-meta">
        <span><span class="pulse-dot"></span>updated {now}</span>
        <span>tap to open →</span>
      </div>
    </div>
    """


@router.get(
    "/partials/usage-summary", response_class=HTMLResponse
)
async def usage_summary_partial():
    from yeti.api.usage import usage_summary

    data = await usage_summary()
    pct = data["budget_used_pct"]
    if pct >= 100:
        bar_color = "var(--red)"
    elif pct >= data["alert_threshold_pct"]:
        bar_color = "var(--yellow)"
    else:
        bar_color = "var(--green)"

    by_model_rows = "".join(
        f'<div class="status-item">'
        f'<span class="name">{m["model"][:30]}</span>'
        f'<span>${m["cost_usd"]:.3f}</span></div>'
        for m in data["by_model"][:5]
    )

    return f"""
    <div style="margin-bottom: 1rem">
      <div style="display:flex; justify-content:space-between;
                  font-size:0.85rem; margin-bottom:0.4rem">
        <span class="muted">This month</span>
        <span>${data["month_paid_usd"]:.2f}
              / ${data["budget_usd"]:.2f}</span>
      </div>
      <div style="background: var(--border); height: 4px;
                  border-radius: 2px; overflow: hidden">
        <div style="background: {bar_color}; height: 100%;
                    width: {min(pct, 100)}%"></div>
      </div>
      <div style="text-align: right; font-size: 0.7rem;
                  color: var(--text-dim); margin-top: 0.2rem">
        {pct:.1f}% used
      </div>
    </div>
    <h3 style="margin-top: 1rem">Top models</h3>
    {by_model_rows or '<div class="muted" style="font-size:0.75rem">No usage yet</div>'}
    """


@router.get(
    "/partials/nav-counts", response_class=HTMLResponse
)
async def nav_counts_partial():
    """OOB-swap counts into the sidebar nav badges.

    Counts only items that need user action:
    - inbox: pending review
    - tasks: ACTIVE + BLOCKED (open, not done)
    - people: high-mention senders without a contact drawer
    """
    inbox_count = 0
    try:
        inbox_count = InboxStore().count_pending()
    except Exception:
        pass

    tasks_count = 0
    try:
        store = TaskStore()
        tasks_count = len(
            store.list(status=TaskStatus.ACTIVE)
        ) + len(store.list(status=TaskStatus.BLOCKED))
    except Exception:
        pass

    people_count = 0
    try:
        from yeti.sleep.gaps import find_gap_senders

        people_count = len(find_gap_senders())
    except Exception:
        pass

    def _badge(slot: str, count: int) -> str:
        cls = "nav-count has-items" if count > 0 else "nav-count"
        body = str(count) if count > 0 else ""
        return (
            f'<span id="nav-count-{slot}" class="{cls}" '
            f'hx-swap-oob="outerHTML">{body}</span>'
        )

    return (
        _badge("inbox", inbox_count)
        + _badge("tasks", tasks_count)
        + _badge("people", people_count)
    )


@router.post("/partials/note", response_class=HTMLResponse)
async def note_partial(
    content: str = Form(...),
    title: str = Form(""),
    context: str = Form(""),
):
    """Capture a note from the dashboard form."""
    from yeti.models.notes import Note, NoteSource, NoteStore

    note = Note(
        content=content,
        title=title,
        context=context,
        source=NoteSource.DASHBOARD,
    )
    store = NoteStore()
    store.create(note)

    try:
        from yeti.worker import triage_note

        triage_note.delay(note.id)
        msg = "queued for triage"
    except Exception:
        msg = "saved (worker offline)"

    return (
        f'<div class="muted" style="font-size:0.85rem">'
        f"Note captured ({note.id[:8]}) — {msg}</div>"
    )


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
    "/partials/tasks", response_class=HTMLResponse
)
async def tasks_partial(status: str = "pending_review"):
    try:
        task_status = TaskStatus(status)
    except ValueError:
        return "<p class='muted'>Invalid status</p>"

    store = TaskStore()
    items = store.list(status=task_status)

    if not items:
        return "<p class='muted'>None</p>"

    rows = []
    for item in items:
        buttons = ""
        if task_status == TaskStatus.ACTIVE:
            buttons = (
                f'<button class="btn btn-success btn-sm" '
                f"onclick=\"markTaskDone('{item.id}')\">"
                f"Done</button> "
                f'<button class="btn btn-ghost btn-sm" '
                f"onclick=\"markTaskCancelled('{item.id}')\">"
                f"Cancel</button>"
            )

        project_tag = ""
        if item.project:
            project_tag = (
                f' <span class="badge badge-dim">'
                f"{item.project}</span>"
            )

        assignee_tag = ""
        if item.assignee and item.assignee != "Daniel":
            assignee_tag = (
                f' <span class="badge badge-dim">'
                f"{item.assignee}</span>"
            )

        rows.append(
            f'<div class="status-row">'
            f'<span style="flex:1">{item.title}'
            f"{project_tag}{assignee_tag}</span>"
            f"<span>{buttons}</span></div>"
        )
    return "\n".join(rows)


@router.get(
    "/partials/tasks-flat", response_class=HTMLResponse
)
async def tasks_flat_partial():
    """Single flat list of tasks with status badges and inline actions."""
    store = TaskStore()
    # Order: active first, then blocked, then completed (limited)
    active = store.list(status=TaskStatus.ACTIVE)
    blocked = store.list(status=TaskStatus.BLOCKED)
    completed = store.list(status=TaskStatus.COMPLETED)[:10]

    if not active and not blocked and not completed:
        return (
            '<div class="muted">'
            "No tasks. Approve proposed tasks from the inbox."
            "</div>"
        )

    rows = []

    def _row(task, status_label, status_color, actions_html):
        project_tag = (
            f' <span class="badge badge-dim">{task.project}</span>'
            if task.project
            else ""
        )
        assignee_tag = (
            f' <span class="badge badge-dim">{task.assignee}</span>'
            if task.assignee and task.assignee != "Daniel"
            else ""
        )
        return (
            f'<div class="status-row">'
            f'<span style="flex:0 0 70px">'
            f'<span class="badge" style="color:{status_color}">'
            f"{status_label}</span></span>"
            f'<span style="flex:1">{task.title}'
            f"{project_tag}{assignee_tag}</span>"
            f'<span class="btn-row">{actions_html}</span>'
            f"</div>"
        )

    for t in active:
        actions = (
            f'<button class="btn btn-success btn-sm" '
            f"onclick=\"markTaskDone('{t.id}')\">Done</button>"
            f'<button class="btn btn-ghost btn-sm" '
            f"onclick=\"markTaskBlocked('{t.id}')\">Block</button>"
            f'<button class="btn btn-ghost btn-sm" '
            f"onclick=\"markTaskCancelled('{t.id}')\">Cancel</button>"
        )
        rows.append(_row(t, "ACTIVE", "var(--green)", actions))

    for t in blocked:
        actions = (
            f'<button class="btn btn-success btn-sm" '
            f"onclick=\"markTaskActive('{t.id}')\">Resume</button>"
            f'<button class="btn btn-ghost btn-sm" '
            f"onclick=\"markTaskCancelled('{t.id}')\">Cancel</button>"
        )
        rows.append(
            _row(t, "BLOCKED", "var(--yellow)", actions)
        )

    for t in completed:
        rows.append(
            _row(
                t,
                "DONE",
                "var(--text-dim)",
                "",
            )
        )

    return "\n".join(rows)


@router.get(
    "/partials/inbox-tile", response_class=HTMLResponse
)
async def inbox_tile_partial():
    """Compact tile showing inbox count + first item summary."""
    store = InboxStore()
    items = store.list_pending()
    count = len(items)

    if count == 0:
        return (
            '<div class="inbox-tile inbox-tile-empty">'
            '<div class="inbox-count">Inbox empty</div>'
            '<div class="inbox-hint muted">'
            "Nothing pending review</div></div>"
        )

    color = "var(--yellow)" if count > 0 else "var(--green)"
    next_titles = [
        f'<li class="muted">{item.title}</li>'
        for item in items[1:5]
    ]
    upcoming = (
        f'<ul class="inbox-upcoming">{"".join(next_titles)}</ul>'
        if next_titles
        else ""
    )

    active = items[0]
    return f"""
    <div class="inbox-tile" style="border-left-color: {color}"
         onclick="window.location.href='/dashboard/inbox'">
      <div class="inbox-count">
        {count} item{"s" if count != 1 else ""} pending
      </div>
      <div class="inbox-active">
        <strong>{active.title}</strong>
      </div>
      {upcoming}
    </div>
    """


@router.get(
    "/partials/inbox-active", response_class=HTMLResponse
)
async def inbox_active_partial():
    """Active item view + upcoming list for the inbox page."""
    store = InboxStore()
    items = store.list_pending()

    if not items:
        return (
            '<div class="card"><h2>Inbox</h2>'
            '<p class="muted">All clear. Nothing pending.</p>'
            "</div>"
        )

    active = items[0]
    upcoming_html = ""
    if len(items) > 1:
        upcoming_items = "".join(
            f'<li>{i.title} <span class="muted">'
            f'({i.type.value})</span></li>'
            for i in items[1:]
        )
        upcoming_html = (
            f'<div class="card"><h2>Up next ({len(items) - 1})</h2>'
            f'<ul class="inbox-upcoming">{upcoming_items}</ul>'
            "</div>"
        )

    body_html, actions_html = _render_inbox_body(active)
    source_html = _render_source_note(active)
    suggestion_html = _render_suggestion(active)

    return f"""
    <div class="card">
      <h2>{active.type.value.replace('_', ' ').title()}</h2>
      <h3 style="margin:0.5rem 0">{active.title}</h3>
      <p class="muted">{active.summary}</p>
      {suggestion_html}
      {body_html}
      <div style="margin-top:1.5rem;display:flex;gap:0.5rem;
                  flex-wrap:wrap">
        {actions_html}
      </div>
    </div>
    {source_html}
    {upcoming_html}
    """


def _render_suggestion(item) -> str:
    """Render the suggestion hint + auto-apply toggle when applicable."""
    sd = getattr(item, "suggested_disposition", "") or ""
    sc = getattr(item, "suggestion_count", 0) or 0
    if not sd or sc < 2:
        return ""
    import html as _html

    pretty = sd.replace("_", " ")
    pattern_key_raw = f"{item.type.value}::{item.title}"
    pattern_key_js = (
        '"'
        + pattern_key_raw.replace("\\", "\\\\")
        .replace('"', '\\"')
        + '"'
    )
    return f"""
    <div class="card" style="background:var(--surface);
                            border-left:2px solid var(--accent);
                            padding:0.5rem 0.75rem;
                            margin:0.5rem 0;font-size:0.85rem">
      <strong>Suggested:</strong>
      <span style="text-transform:capitalize">{_html.escape(pretty)}</span>
      <span class="muted">(based on {sc} prior similar
       resolution{'s' if sc != 1 else ''})</span>
      <button
        type="button"
        style="margin-left:0.75rem;font-size:0.75rem"
        onclick="window.toggleAutoApply(
          {pattern_key_js}, true).catch(console.error)"
      >Auto-apply next time</button>
    </div>
    """


def _resolve_note_id(item) -> str:
    """Find a source note id, falling back to parsing the source field."""
    if item.source_note_id:
        return item.source_note_id
    src = item.source or ""
    # Source patterns: "triage:<id>" or "note:<id>"
    if ":" in src:
        prefix, _, note_id = src.partition(":")
        if prefix in ("triage", "note") and note_id:
            return note_id
    return ""


def _render_source_note(item) -> str:
    """If the item has a source note, render it as a collapsible card."""
    note_id = _resolve_note_id(item)
    if not note_id:
        return ""

    from yeti.models.notes import NoteStore

    store = NoteStore()
    note = store.get(note_id)
    if not note:
        return ""

    import html as _html

    captured = note.created_at.split("T")[0]
    title = note.title or "Source note"
    content_html = _html.escape(note.content or "")
    context_html = (
        f'<div class="muted" style="font-size:0.75rem;'
        f'margin-bottom:0.5rem">Context: '
        f"{_html.escape(note.context)}</div>"
        if note.context
        else ""
    )

    return f"""
    <div class="card">
      <h2>Source: {_html.escape(title)}</h2>
      <div class="muted" style="font-size:0.75rem;
                                margin-bottom:0.5rem">
        Captured {captured} via {note.source.value}
      </div>
      {context_html}
      <pre style="white-space:pre-wrap;font-size:0.8rem;
                  color:var(--text-soft);max-height:400px;
                  overflow-y:auto;background:var(--bg);
                  padding:0.75rem;border-radius:4px;
                  border:1px solid var(--border);
                  font-family:inherit">{content_html}</pre>
    </div>
    """


def _resolve_btn(
    item_id: str,
    label: str,
    resolution: str,
    style: str = "ghost",
) -> str:
    """Build a resolve button using the unified btn classes."""
    return (
        f'<button type="button" class="btn btn-{style}" '
        f"onclick=\"resolveInboxItem('{item_id}', "
        f"'{resolution}')\">{label}</button>"
    )


def _render_inbox_body(item) -> tuple[str, str]:
    """Render the body and action buttons for an inbox item.

    Generic schema-driven approach: render the answer_schema as a form.
    Image fallback is the only special-cased renderer.
    """
    from yeti.models.inbox import InboxType

    payload = item.payload or {}

    # Image fallback is the only special case (image is the form)
    if (
        item.type == InboxType.NOTIFICATION
        and payload.get("image_id")
    ):
        return _render_image_fallback(item, payload)

    return _render_schema_form(item)


def _render_schema_form(item) -> tuple[str, str]:
    """Render a clarification with answer_schema as a generic form."""
    schema = item.answer_schema or [
        {"key": "answer", "label": "", "type": "textarea"}
    ]
    quick_actions = item.quick_actions or [
        "discard",
        "convert_to_task",
    ]

    # Source note link
    note_link = ""
    if item.source_note_id:
        note_link = (
            f'<div style="margin-bottom:0.75rem">'
            f'<a href="/api/notes/{item.source_note_id}" '
            f'target="_blank" '
            f'style="color:var(--accent);font-size:0.8rem">'
            f"View source note →</a></div>"
        )

    # Render fields
    field_html = []
    for idx, field in enumerate(schema):
        key = field.get("key", f"field_{idx}")
        label = field.get("label", "")
        ftype = field.get("type", "text")
        value = field.get("value", "") or ""
        options = field.get("options", [])
        input_id = f"answer-{item.id}-{key}"

        label_html = (
            f'<label class="muted" '
            f'style="font-size:0.75rem;display:block;'
            f'margin-bottom:0.25rem">{label}</label>'
            if label
            else ""
        )

        if ftype == "choice" and options:
            choices = "".join(
                f'<button type="button" class="btn btn-sm '
                f'choice-btn" data-key="{key}" '
                f'data-input="{input_id}" '
                f'data-value="{opt}" '
                f"onclick=\"pickChoice(this)\">{opt}</button>"
                for opt in options
            )
            input_html = (
                f'<input type="hidden" id="{input_id}" '
                f'data-key="{key}" value="{value}"/>'
                f'<div class="btn-row" style="margin-bottom:0.75rem">'
                f"{choices}</div>"
            )
        elif ftype == "textarea":
            input_html = (
                f'<textarea id="{input_id}" data-key="{key}" '
                f'rows="3" '
                f'style="width:100%;padding:0.55rem 0.75rem;'
                f"background:var(--bg);"
                f"border:1px solid var(--border);"
                f"border-radius:4px;color:var(--text);"
                f"font-size:0.85rem;font-family:inherit;"
                f"resize:vertical;outline:none;"
                f'margin-bottom:0.75rem">{value}</textarea>'
            )
        else:  # text
            input_html = (
                f'<input type="text" id="{input_id}" '
                f'data-key="{key}" value="{value}" '
                f'style="width:100%;padding:0.55rem 0.75rem;'
                f"background:var(--bg);"
                f"border:1px solid var(--border);"
                f"border-radius:4px;color:var(--text);"
                f"font-size:0.85rem;font-family:inherit;"
                f'outline:none;margin-bottom:0.75rem"/>'
            )

        field_html.append(
            f'<div>{label_html}{input_html}</div>'
        )

    body = f"""
    {note_link}
    <form id="answer-form-{item.id}" onsubmit="return false">
      {''.join(field_html)}
    </form>
    """

    # Build actions — proposed actions get a different submit handler
    from yeti.models.inbox import InboxType

    is_proposed_action = (
        item.type == InboxType.PROPOSED_ACTION
    )
    submit_label = (
        "Approve & Create Task"
        if is_proposed_action
        else "Send answer"
    )
    submit_handler = (
        "submitProposedTask"
        if is_proposed_action
        else "submitAnswer"
    )
    submit_btn = (
        f'<button type="button" class="btn btn-primary" '
        f"onclick=\"{submit_handler}('{item.id}')\">"
        f"{submit_label}</button>"
    )

    quick_action_buttons = []
    if "convert_to_task" in quick_actions:
        quick_action_buttons.append(
            f'<button type="button" class="btn btn-ghost" '
            f"onclick=\"convertToTask('{item.id}', "
            f"'{_escape(item.title)}')\">Convert to task</button>"
        )
    if "discard" in quick_actions:
        quick_action_buttons.append(
            _resolve_btn(
                item.id, "Discard", "discarded", "ghost"
            )
        )

    actions = submit_btn + "".join(quick_action_buttons) + """
    <div id="inbox-status" class="muted"
         style="font-size:0.8rem;margin-top:0.75rem;
                min-height:1.2rem"></div>
    <script>
    function pickChoice(btn) {
      const input = document.getElementById(btn.dataset.input);
      input.value = btn.dataset.value;
      const siblings = btn.parentElement
        .querySelectorAll('.choice-btn');
      siblings.forEach(s => s.classList.remove('btn-primary'));
      siblings.forEach(s => s.classList.add('btn-ghost'));
      btn.classList.remove('btn-ghost');
      btn.classList.add('btn-primary');
    }
    function setBusy(buttonsParent, busy, label) {
      const status = document.getElementById('inbox-status');
      if (status) status.textContent = label || '';
      const btns = document.querySelectorAll(
        '.btn-primary, .btn-success, .btn-ghost, .btn-danger'
      );
      btns.forEach(b => {
        b.disabled = busy;
        b.style.opacity = busy ? '0.5' : '';
        b.style.pointerEvents = busy ? 'none' : '';
      });
    }
    function _collectAnswer(itemId) {
      const form = document.getElementById('answer-form-' + itemId);
      const inputs = form.querySelectorAll('[data-key]');
      const answer = {};
      inputs.forEach(el => {
        if (el.value) answer[el.dataset.key] = el.value;
      });
      return answer;
    }
    async function submitAnswer(itemId) {
      const answer = _collectAnswer(itemId);
      setBusy(null, true, 'Sending answer to YETI...');
      try {
        const r = await fetch('/api/inbox/' + itemId + '/answer', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          credentials: 'include',
          body: JSON.stringify({answer: answer})
        });
        if (r.ok) {
          const data = await r.json();
          setBusy(null, false,
            'Saved (' + data.facts_applied + ' facts). Loading next...');
          clearAndRefresh();
        } else {
          setBusy(null, false, 'Failed to send answer');
        }
      } catch(e) {
        setBusy(null, false, 'Error: ' + e.message);
      }
    }
    async function submitProposedTask(itemId) {
      const answer = _collectAnswer(itemId);
      if (!answer.title) {
        setBusy(null, false, 'Title required');
        return;
      }
      setBusy(null, true, 'Creating task...');
      try {
        const r = await fetch(
          '/api/inbox/' + itemId + '/approve-task', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          credentials: 'include',
          body: JSON.stringify({answer: answer})
        });
        if (r.ok) {
          setBusy(null, false, 'Task created. Loading next...');
          clearAndRefresh();
        } else {
          setBusy(null, false, 'Failed to create task');
        }
      } catch(e) {
        setBusy(null, false, 'Error: ' + e.message);
      }
    }
    async function convertToTask(itemId, defaultTitle) {
      const title = prompt('Task title:', defaultTitle);
      if (!title) return;
      setBusy(null, true, 'Converting to task...');
      try {
        const r = await fetch(
          '/api/inbox/' + itemId + '/convert-to-task', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          credentials: 'include',
          body: JSON.stringify({title: title})
        });
        if (r.ok) {
          setBusy(null, false, 'Task created. Loading next...');
          clearAndRefresh();
        } else {
          setBusy(null, false, 'Failed');
        }
      } catch(e) {
        setBusy(null, false, 'Error: ' + e.message);
      }
    }
    </script>
    """
    return body, actions


def _escape(text: str) -> str:
    """Escape for use inside a JS single-quoted string."""
    return (
        text.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace("\n", " ")
    )


def _render_image_fallback(
    item, payload: dict
) -> tuple[str, str]:
    """Render manual review form for an image-fallback inbox item."""
    image_id = payload.get("image_id", "")
    raw = payload.get("raw_text", "")
    extracted = payload.get("extracted") or {}

    fields = [
        ("name", "Name"),
        ("company", "Company"),
        ("title", "Title"),
        ("email", "Email"),
        ("phone", "Phone"),
        ("address", "Address"),
        ("website", "Website"),
    ]
    inputs = "".join(
        f'<div style="margin-bottom:0.5rem">'
        f'<label class="muted" style="font-size:0.75rem">'
        f"{label}</label>"
        f'<input type="text" '
        f'id="img-{item.id}-{key}" '
        f'value="{extracted.get(key, "")}" '
        f'style="width:100%;padding:0.5rem 0.6rem;'
        f"background:transparent;border:1px solid var(--border);"
        f"border-radius:4px;color:var(--text);"
        f'font-size:0.85rem;margin-top:0.2rem"/>'
        f"</div>"
        for key, label in fields
    )

    raw_html = (
        f'<details style="margin-top:1rem">'
        f'<summary class="muted" style="cursor:pointer;'
        f'font-size:0.8rem">Raw OCR ({len(raw)} chars)</summary>'
        f'<pre style="white-space:pre-wrap;font-size:0.75rem;'
        f'color:var(--text-dim);margin-top:0.5rem">{raw}</pre>'
        f"</details>"
        if raw
        else ""
    )

    body = f"""
    <div style="display:grid;grid-template-columns:1fr 1fr;
                gap:1rem;margin-top:1rem">
      <div>
        <img src="/api/images/{image_id}"
             style="max-width:100%;max-height:400px;
                    border:1px solid var(--border);border-radius:4px"/>
      </div>
      <div>
        {inputs}
      </div>
    </div>
    {raw_html}
    """

    field_keys = ",".join(f"'{k}'" for k, _ in fields)
    actions = f"""
    <button class="btn btn-success"
            onclick="saveImageReview('{item.id}', [{field_keys}])">
      Save & Store
    </button>
    <button class="btn btn-ghost"
            hx-post="/api/inbox/{item.id}/resolve"
            hx-vals='{{"resolution":"discarded"}}'
            hx-swap="none"
            hx-on::after-request="clearAndRefresh()">
      Discard
    </button>
    <script>
    function saveImageReview(itemId, fields) {{
      const data = {{}};
      fields.forEach(f => {{
        const el = document.getElementById('img-' + itemId + '-' + f);
        if (el && el.value) data[f] = el.value;
      }});
      fetch('/api/inbox/' + itemId + '/resolve', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        credentials: 'include',
        body: JSON.stringify({{
          resolution: 'manual_save',
          note: JSON.stringify(data)
        }})
      }}).then(() => clearAndRefresh());
    }}
    </script>
    """
    return body, actions


def _dot_for(state: str) -> str:
    if state in ("up", "connected"):
        css = "dot-green"
    elif state in ("unknown", "needs_auth"):
        css = "dot-yellow"
    elif state == "not_configured":
        css = "dot-dim"
    else:
        css = "dot-red"
    return f'<span class="dot {css}"></span>'


# --- People page partials ---


def _escape(s: str) -> str:
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


@router.get(
    "/partials/people-needs-profile",
    response_class=HTMLResponse,
)
async def people_needs_profile_partial():
    """High-mention senders with no contact drawer."""
    try:
        from yeti.sleep.gaps import find_gap_senders

        gaps = find_gap_senders()
    except Exception:
        return (
            '<div class="muted">'
            "Could not load (worker / db unavailable)."
            "</div>"
        )

    if not gaps:
        return (
            '<div class="muted" style="font-size:0.85rem">'
            "No unknown frequent contacts."
            "</div>"
        )

    rows = []
    for g in gaps:
        name = g.get("name") or ""
        email = g.get("email") or ""
        count = int(g.get("count") or 0)
        display = name or email
        last = (g.get("last_seen") or "")[:10]
        meta = (
            f"{count} email{'s' if count != 1 else ''}"
            + (f" · last {last}" if last else "")
        )
        rows.append(
            f'<div class="status-row">'
            f'<span style="flex:1;min-width:0">'
            f'<strong>{_escape(display)}</strong>'
            f'<div class="muted" '
            f'style="font-size:0.75rem">'
            f"{_escape(email)} · {meta}</div>"
            f"</span>"
            f'<span class="btn-row">'
            f'<button class="btn btn-primary btn-sm" '
            f"onclick=\"askYetiAbout("
            f"'{_escape(name)}','{_escape(email)}',{count})\">"
            f"Ask in chat</button>"
            f'<a class="btn btn-ghost btn-sm" '
            f'href="/dashboard/inbox">Inbox form</a>'
            f"</span>"
            f"</div>"
        )
    return "\n".join(rows)


def _list_contact_drawers(
    query: str = "", limit: int = 100
) -> list[dict]:
    """Return drawers in wing=people, room=contacts.

    Direct ChromaDB read (same pattern as sleep/gaps.py). Filters
    superseded ids, then optionally narrows by case-insensitive
    substring on the document text.
    """
    try:
        import chromadb

        from yeti.memory.client import MemPalaceClient
    except Exception:
        return []
    try:
        client = MemPalaceClient()
        col = chromadb.PersistentClient(
            path=client.palace_path
        ).get_collection("mempalace_drawers")
        page = col.get(
            where={
                "$and": [
                    {"wing": "people"},
                    {"room": "contacts"},
                ]
            },
            include=["documents", "metadatas"],
        )
    except Exception:
        return []

    ids = page.get("ids") or []
    docs = page.get("documents") or []
    metas = page.get("metadatas") or []

    try:
        from yeti.models.superseded import SupersededStore

        blocked = SupersededStore().superseded_ids()
    except Exception:
        blocked = set()

    q = (query or "").strip().lower()
    items: list[dict] = []
    for i, doc in enumerate(docs):
        drawer_id = ids[i] if i < len(ids) else ""
        if drawer_id in blocked:
            continue
        text = doc or ""
        if q and q not in text.lower():
            continue
        meta = metas[i] if i < len(metas) else {}
        # First non-empty line is usually "Name: ..."
        first_line = ""
        for line in text.splitlines():
            line = line.strip()
            if line:
                first_line = line
                break
        name = first_line
        if name.lower().startswith("name:"):
            name = name.split(":", 1)[1].strip()
        items.append(
            {
                "id": drawer_id,
                "name": name or "(unnamed)",
                "preview": text[:160],
                "source": (meta or {}).get("source", ""),
            }
        )
        if len(items) >= limit:
            break
    items.sort(key=lambda it: it["name"].lower())
    return items


@router.get(
    "/partials/people-contacts", response_class=HTMLResponse
)
async def people_contacts_partial(q: str = ""):
    items = _list_contact_drawers(query=q, limit=200)
    if not items:
        msg = (
            "No matches."
            if q
            else "No contact drawers yet. Run "
            "<code>yeti setup-self</code> to seed yours."
        )
        return f'<div class="muted">{msg}</div>'

    rows = []
    for it in items:
        src_tag = ""
        if it["source"]:
            src_tag = (
                f' <span class="badge badge-dim">'
                f'{_escape(it["source"])}</span>'
            )
        rows.append(
            f'<div class="status-row" '
            f'style="cursor:pointer" '
            f"onclick=\"openContact('{_escape(it['id'])}',"
            f"'{_escape(it['name'])}')\">"
            f'<span style="flex:1;min-width:0">'
            f'<strong>{_escape(it["name"])}</strong>'
            f"{src_tag}"
            f'<div class="muted" '
            f'style="font-size:0.75rem;'
            f"overflow:hidden;text-overflow:ellipsis;"
            f'white-space:nowrap">'
            f'{_escape(it["preview"])}</div>'
            f"</span>"
            f"</div>"
        )
    return "\n".join(rows)


@router.get(
    "/partials/people-contact-body",
    response_class=HTMLResponse,
)
async def people_contact_body_partial(id: str):
    """Return the raw drawer body text for a contact."""
    try:
        import chromadb

        from yeti.memory.client import MemPalaceClient
    except Exception:
        return "Unavailable."
    try:
        client = MemPalaceClient()
        col = chromadb.PersistentClient(
            path=client.palace_path
        ).get_collection("mempalace_drawers")
        page = col.get(ids=[id], include=["documents"])
    except Exception:
        return "Could not load drawer."
    docs = page.get("documents") or []
    if not docs:
        return "Drawer not found."
    return docs[0] or "(empty)"
