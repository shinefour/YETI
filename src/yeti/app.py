"""YETI FastAPI application."""

import logging
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx as _httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse

from yeti.agents.chat import chat as chat_agent
from yeti.api.images import router as images_router
from yeti.api.inbox import router as inbox_router
from yeti.api.integrations import router as integrations_router
from yeti.api.memory import router as memory_router
from yeti.api.notes import router as notes_router
from yeti.api.tasks import router as tasks_router
from yeti.api.usage import router as usage_router
from yeti.config import settings
from yeti.dashboard.routes import router as dashboard_router
from yeti.integrations.jira import JiraAdapter
from yeti.integrations.notion import NotionAdapter

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    app.state.started_at = datetime.now(UTC)

    try:
        from yeti.migrations import run_all

        run_all()
    except Exception:
        logger.exception("Migrations failed")

    yield


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(tasks_router)
app.include_router(inbox_router)
app.include_router(notes_router)
app.include_router(memory_router)
app.include_router(images_router)
app.include_router(usage_router)
app.include_router(integrations_router)
app.include_router(dashboard_router)

# Paths that don't require auth
_PUBLIC_PATHS = {"/health", "/webhooks"}


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Require API key for all routes except health and webhooks."""
    path = request.url.path

    if not settings.dashboard_api_key:
        return await call_next(request)

    if path == "/health" or path.startswith("/webhooks"):
        return await call_next(request)

    # Check cookie (dashboard sessions)
    session_token = request.cookies.get("yeti_session")
    if session_token and secrets.compare_digest(
        session_token, settings.dashboard_api_key
    ):
        return await call_next(request)

    # Check header (API clients / CLI)
    api_key = request.headers.get("x-api-key", "")
    if api_key and secrets.compare_digest(
        api_key, settings.dashboard_api_key
    ):
        return await call_next(request)

    # Check query param (login redirect)
    key_param = request.query_params.get("key", "")
    if key_param and secrets.compare_digest(
        key_param, settings.dashboard_api_key
    ):
        response = RedirectResponse(url="/dashboard")
        response.set_cookie(
            "yeti_session",
            settings.dashboard_api_key,
            httponly=True,
            secure=True,
            samesite="strict",
            max_age=86400 * 30,
        )
        return response

    # Login page
    if path == "/login":
        return await call_next(request)

    return JSONResponse({"error": "Unauthorized"}, status_code=401)


@app.get("/")
async def root():
    return RedirectResponse(url="/dashboard")


@app.get("/login")
async def login_page():
    return JSONResponse(
        {
            "message": "YETI requires authentication.",
            "hint": "Append ?key=<your-api-key> or "
            "send x-api-key header.",
        },
        status_code=401,
    )


@app.get("/health")
async def health():
    """Health check endpoint — used by kamal-proxy to verify the container is ready."""
    return JSONResponse(
        {"status": "healthy", "version": "0.1.0"},
    )


async def get_system_status() -> dict:
    """Collect system status — shared by API and dashboard."""
    services = {"api": "up"}

    try:
        import redis as _redis

        r = _redis.from_url(settings.redis_url, socket_timeout=2)
        r.ping()
        services["redis"] = "up"
    except Exception:
        services["redis"] = "down"

    for name, url in [
        ("chromadb", f"{settings.chromadb_url}/api/v2/heartbeat"),
        ("ollama", f"{settings.ollama_base_url}/api/tags"),
    ]:
        try:
            async with _httpx.AsyncClient(timeout=2) as c:
                r = await c.get(url)
                services[name] = "up" if r.is_success else "down"
        except Exception:
            services[name] = "down"

    integrations = {}
    jira = JiraAdapter()
    integrations["jira"] = (
        "connected" if await jira.health() else (
            "not_configured"
            if not settings.jira_url
            else "error"
        )
    )
    notion = NotionAdapter()
    integrations["notion"] = (
        "connected" if await notion.health() else (
            "not_configured"
            if not settings.notion_api_key
            else "error"
        )
    )
    for name in ["teams", "slack", "calendar", "email"]:
        integrations[name] = "not_configured"

    return {"services": services, "integrations": integrations}


@app.get("/api/status")
async def status():
    """System status — integration health, service states."""
    data = await get_system_status()
    data["status"] = "healthy"
    data["started_at"] = getattr(
        app.state, "started_at", datetime.now(UTC)
    ).isoformat()
    return JSONResponse(data)


@app.post("/api/chat")
async def chat(message: dict):
    """Chat endpoint — receives a message, routes to the Chat Agent."""
    user_message = message.get("message", "")
    if not user_message:
        return JSONResponse(
            {"error": "No message provided"}, status_code=400
        )

    if not settings.anthropic_api_key:
        return JSONResponse(
            {"error": "YETI_ANTHROPIC_API_KEY not configured"},
            status_code=503,
        )

    history = message.get("history", [])

    try:
        response = await chat_agent(user_message, history or None)
        return JSONResponse({"response": response})
    except Exception:
        logger.exception("Chat agent error")
        return JSONResponse(
            {"error": "Chat agent failed. Check logs."},
            status_code=500,
        )


@app.post("/webhooks/{integration}")
async def webhook_receiver(integration: str, payload: dict):
    """Webhook receiver — dispatches incoming webhooks to the Triage Agent."""
    # TODO: Route to Triage Agent
    return JSONResponse(
        {"received": True, "integration": integration},
    )
