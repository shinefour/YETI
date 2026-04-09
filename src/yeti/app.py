"""YETI FastAPI application."""

import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx as _httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from yeti.agents.chat import chat as chat_agent
from yeti.api.actions import router as actions_router
from yeti.config import settings
from yeti.dashboard.routes import router as dashboard_router
from yeti.integrations.jira import JiraAdapter
from yeti.integrations.notion import NotionAdapter

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    app.state.started_at = datetime.now(UTC)
    yield


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(actions_router)
app.include_router(dashboard_router)


@app.get("/health")
async def health():
    """Health check endpoint — used by kamal-proxy to verify the container is ready."""
    return JSONResponse(
        {"status": "healthy", "version": "0.1.0"},
    )


@app.get("/api/status")
async def status():
    """System status — integration health, service states."""
    services = {"api": "up"}

    # Check Redis
    try:
        import redis as _redis

        r = _redis.from_url(settings.redis_url, socket_timeout=2)
        r.ping()
        services["redis"] = "up"
    except Exception:
        services["redis"] = "down"

    # Check accessory services via HTTP
    for name, url in [
        ("mempalace", settings.mempalace_url),
        ("chromadb", f"{settings.chromadb_url}/api/v2/heartbeat"),
        ("ollama", f"{settings.ollama_base_url}/api/tags"),
    ]:
        try:
            async with _httpx.AsyncClient(timeout=2) as c:
                r = await c.get(url)
                services[name] = "up" if r.is_success else "down"
        except Exception:
            services[name] = "down"

    # Check integrations
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

    return JSONResponse(
        {
            "status": "healthy",
            "started_at": getattr(
                app.state, "started_at", datetime.now(UTC)
            ).isoformat(),
            "services": services,
            "integrations": integrations,
        },
    )


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
