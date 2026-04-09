"""YETI FastAPI application."""

import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from yeti.agents.chat import chat as chat_agent
from yeti.config import settings
from yeti.dashboard.routes import router as dashboard_router

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
    return JSONResponse(
        {
            "status": "healthy",
            "started_at": getattr(
                app.state, "started_at", datetime.now(UTC)
            ).isoformat(),
            "services": {
                "api": "up",
                "redis": "unknown",
                "mempalace": "unknown",
                "chromadb": "unknown",
                "ollama": "unknown",
            },
            "integrations": {
                "teams": "not_configured",
                "slack": "not_configured",
                "jira": "not_configured",
                "notion": "not_configured",
                "calendar": "not_configured",
                "email": "not_configured",
            },
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
