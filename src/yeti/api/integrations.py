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


@router.post("/outlook/token")
async def upload_outlook_token(body: dict):
    """Receive an Outlook OAuth token cache from the CLI flow."""
    from yeti.config import settings
    from yeti.integrations.outlook import save_token_blob

    mailbox = (body.get("mailbox") or "").strip().lower()
    token = body.get("token", "")
    if not mailbox or not token:
        return JSONResponse(
            {"error": "mailbox and token fields required"},
            status_code=400,
        )

    if mailbox not in settings.outlook_mailbox_map():
        return JSONResponse(
            {
                "error": (
                    f"{mailbox} not listed in "
                    "YETI_OUTLOOK_MAILBOXES"
                )
            },
            status_code=400,
        )

    try:
        path = save_token_blob(mailbox, token)
    except Exception as e:
        logger.exception(
            "Failed to save Outlook token for %s", mailbox
        )
        return JSONResponse(
            {"error": str(e)}, status_code=500
        )

    return {"saved": True, "mailbox": mailbox, "path": str(path)}


@router.get("/outlook/status")
async def outlook_status():
    """Per-mailbox Outlook credential status."""
    from yeti.config import settings
    from yeti.integrations.outlook import credential_status

    result = {}
    for email, wing in settings.outlook_mailbox_map().items():
        status = credential_status(email)
        status["wing"] = wing
        result[email] = status
    return result
