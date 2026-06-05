"""YETI FastAPI application."""

import logging
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx as _httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from yeti.agents.chat import chat as chat_agent
from yeti.api.entity import router as entity_router
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
from yeti.mcp_server import http_app as _build_mcp_http_app

logger = logging.getLogger(__name__)


_mcp_http_app = _build_mcp_http_app()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    app.state.started_at = datetime.now(UTC)

    try:
        from yeti.migrations import run_all

        run_all()
    except Exception:
        logger.exception("Migrations failed")

    async with _mcp_http_app.lifespan(app):
        yield


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
    # Auto trailing-slash redirects break the MCP bearer flow: claude.ai
    # POSTs /mcp, FastAPI 307s to /mcp/, the client drops the
    # Authorization header on the redirect, the MCP transport returns
    # 401, and the connector reports "Authorization with the MCP server
    # failed". Serve both /mcp and /mcp/ directly instead.
    redirect_slashes=False,
)
app.include_router(tasks_router)
app.include_router(inbox_router)
app.include_router(notes_router)
app.include_router(memory_router)
app.include_router(entity_router)
app.include_router(images_router)
app.include_router(usage_router)
app.include_router(integrations_router)
app.include_router(dashboard_router)

_STATIC_DIR = Path(__file__).parent / "dashboard" / "static"
_STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount(
    "/static", StaticFiles(directory=str(_STATIC_DIR)), name="static"
)
app.mount("/mcp", _mcp_http_app)

# Paths that don't require auth
_PUBLIC_PATHS = {"/health", "/webhooks"}
_PUBLIC_PREFIXES = ("/static/", "/favicon")


@app.middleware("http")
async def normalize_mcp_path(request: Request, call_next):
    """Rewrite /mcp -> /mcp/ and /dashboard -> /dashboard/ in-place.

    redirect_slashes is disabled so we don't 307 claude.ai (which then
    drops its Bearer header); the trade-off is that mounted/prefixed
    apps only match the trailing-slash form, so bare paths land on
    a 404. Mutate the ASGI path before routing instead.
    """
    if request.scope["path"] == "/mcp":
        request.scope["path"] = "/mcp/"
        request.scope["raw_path"] = b"/mcp/"
    elif request.scope["path"] == "/dashboard":
        request.scope["path"] = "/dashboard/"
        request.scope["raw_path"] = b"/dashboard/"
    return await call_next(request)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Require API key for all routes except health and webhooks."""
    path = request.url.path

    if not settings.dashboard_api_key:
        return await call_next(request)

    if path == "/health" or path.startswith("/webhooks"):
        return await call_next(request)

    if path.startswith(_PUBLIC_PREFIXES):
        return await call_next(request)

    # MCP: the mounted server enforces its own OAuth 2.1 auth, so we let
    # the parent middleware pass everything under /mcp through. claude.ai
    # connector UI does not allow custom headers and treats OAuth as
    # mandatory, hence the in-memory OAuth provider inside the MCP server.
    if path == "/mcp" or path.startswith("/mcp/"):
        return await call_next(request)

    # RFC 9728 Protected Resource Metadata + OAuth Authorization Server
    # discovery live under /.well-known/. The MCP transport advertises
    # these from inside the 401 WWW-Authenticate header, so they must
    # be reachable without the dashboard api key.
    if path.startswith("/.well-known/"):
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
        response = RedirectResponse(url="/dashboard/")
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
    return RedirectResponse(url="/dashboard/")


@app.get("/favicon.ico", include_in_schema=False)
@app.get("/favicon.svg", include_in_schema=False)
async def favicon():
    return FileResponse(
        _STATIC_DIR / "yeti.svg", media_type="image/svg+xml"
    )


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


@app.get("/.well-known/oauth-authorization-server/mcp")
@app.get("/.well-known/oauth-authorization-server/mcp/")
@app.get("/.well-known/oauth-authorization-server")
async def oauth_authorization_server_metadata():
    """RFC 8414 Authorization Server Metadata for the MCP issuer.

    FastMCP serves this at /mcp/.well-known/oauth-authorization-server,
    but RFC 8414 §3 says clients discover by inserting the well-known
    suffix between host and path of the issuer URL — so for issuer
    https://yeti.diconve.com/mcp the canonical location is
    https://yeti.diconve.com/.well-known/oauth-authorization-server/mcp.
    claude.ai uses the path-suffix variant; without this duplicate
    route the connector cannot register dynamically.
    """
    public = (
        settings.dashboard_public_url or "http://localhost:8000"
    ).rstrip("/")
    base = f"{public}/mcp"
    return JSONResponse(
        {
            "issuer": base,
            "authorization_endpoint": f"{base}/authorize",
            "token_endpoint": f"{base}/token",
            "registration_endpoint": f"{base}/register",
            "response_types_supported": ["code"],
            "grant_types_supported": [
                "authorization_code",
                "refresh_token",
            ],
            "token_endpoint_auth_methods_supported": [
                "client_secret_post",
                "client_secret_basic",
            ],
            "code_challenge_methods_supported": ["S256"],
        }
    )


@app.get("/.well-known/oauth-protected-resource/mcp/")
@app.get("/.well-known/oauth-protected-resource/mcp")
async def oauth_protected_resource_mcp():
    """RFC 9728 Protected Resource Metadata for the MCP server.

    The mounted FastMCP app serves this same document at
    /mcp/.well-known/oauth-protected-resource/mcp/, but the
    WWW-Authenticate header it emits points at the root
    /.well-known/... URL — which 404s under the parent FastAPI. We
    proxy the metadata at the advertised location so OAuth-aware
    clients (claude.ai connector, MCP Inspector) can finish discovery.
    """
    public = (
        settings.dashboard_public_url or "http://localhost:8000"
    ).rstrip("/")
    return JSONResponse(
        {
            "resource": f"{public}/mcp/",
            "authorization_servers": [f"{public}/mcp"],
            "scopes_supported": [],
            "bearer_methods_supported": ["header"],
        }
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

    # Gmail
    if settings.gmail_client_id:
        try:
            from yeti.integrations.gmail import GmailAdapter

            gmail = GmailAdapter()
            integrations["gmail"] = (
                "connected"
                if await gmail.health()
                else "needs_auth"
            )
        except Exception:
            integrations["gmail"] = "error"
    else:
        integrations["gmail"] = "not_configured"

    # Outlook (per mailbox)
    outlook_map = settings.outlook_mailbox_map()
    if outlook_map and settings.microsoft_client_id:
        from yeti.integrations.outlook import OutlookAdapter

        for email in outlook_map:
            key = f"outlook:{email}"
            try:
                ok = await OutlookAdapter(email).health()
                integrations[key] = (
                    "connected" if ok else "needs_auth"
                )
            except Exception:
                integrations[key] = "error"
    elif not settings.microsoft_client_id:
        integrations["outlook"] = "not_configured"

    for name in ["teams", "slack", "calendar"]:
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
