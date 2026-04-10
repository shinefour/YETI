"""Integration management API — credentials, OAuth tokens, etc."""

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/integrations", tags=["integrations"]
)


@router.post("/gmail/token")
async def upload_gmail_token(body: dict):
    """Receive a Gmail OAuth token JSON blob from the CLI flow."""
    from yeti.integrations.gmail import (
        TOKEN_PATH,
        save_token_blob,
    )

    token = body.get("token", "")
    if not token:
        return JSONResponse(
            {"error": "token field required"}, status_code=400
        )

    try:
        save_token_blob(token)
    except Exception as e:
        logger.exception("Failed to save Gmail token")
        return JSONResponse(
            {"error": str(e)}, status_code=500
        )

    return {"saved": True, "path": str(TOKEN_PATH)}


@router.get("/gmail/status")
async def gmail_status():
    """Check Gmail credential status."""
    from yeti.integrations.gmail import (
        TOKEN_PATH,
        load_credentials,
    )

    if not TOKEN_PATH.exists():
        return {
            "configured": False,
            "reason": "No token file. Run yeti gmail-auth.",
        }

    creds = load_credentials()
    if not creds:
        return {
            "configured": False,
            "reason": "Token invalid or could not refresh.",
        }

    return {
        "configured": True,
        "expired": creds.expired,
        "has_refresh": bool(creds.refresh_token),
    }
