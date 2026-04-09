"""YETI FastAPI application."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from yeti.config import settings


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
        return JSONResponse({"error": "No message provided"}, status_code=400)

    # TODO: Route to Chat Agent (PydanticAI)
    return JSONResponse(
        {
            "response": f"YETI received: {user_message}",
            "note": "Chat Agent not yet connected — this is a placeholder response.",
        },
    )


@app.post("/webhooks/{integration}")
async def webhook_receiver(integration: str, payload: dict):
    """Webhook receiver — dispatches incoming webhooks to the Triage Agent."""
    # TODO: Route to Triage Agent
    return JSONResponse(
        {"received": True, "integration": integration},
    )
