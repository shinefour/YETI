"""Outlook / Microsoft Graph integration via MSAL.

One OAuth grant per mailbox — each mailbox is pinned to a specific wing
via `YETI_OUTLOOK_MAILBOXES`. Token cache persisted per mailbox on disk.

Setup:
  1. Create an Azure AD app registration ("YETI").
  2. Auth -> Mobile and desktop apps: add redirect URI `http://localhost`,
     enable "Allow public client flows".
  3. API permissions (delegated): Mail.Read, Mail.Send, offline_access,
     User.Read.
  4. Set YETI_MICROSOFT_CLIENT_ID, YETI_OUTLOOK_MAILBOXES.
  5. Run `yeti outlook-auth <mailbox>` once per mailbox.
  6. Sync runs automatically via Celery.
"""

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from yeti.config import settings

logger = logging.getLogger(__name__)

SCOPES = [
    "Mail.Read",
    "Mail.Send",
    "User.Read",
]
# `offline_access` is automatically added by MSAL for public clients.

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
TOKEN_DIR = Path("/data/yeti/outlook")


def _authority() -> str:
    """Pick the MSAL authority URL.

    Prefers a tenant-specific endpoint when YETI_MICROSOFT_TENANT_ID is
    set (required for single-tenant app registrations). Falls back to
    `/common` for multi-tenant apps.
    """
    tenant = (settings.microsoft_tenant_id or "").strip()
    if tenant:
        return f"https://login.microsoftonline.com/{tenant}"
    return "https://login.microsoftonline.com/common"


def _slug(email: str) -> str:
    """Filesystem-safe identifier for a mailbox email."""
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", email.strip().lower())


def token_path_for(email: str, base: Path = TOKEN_DIR) -> Path:
    return base / f"{_slug(email)}.json"


def _build_msal_app(cache: "Any | None" = None):
    """Build a PublicClientApplication with optional token cache."""
    import msal

    if not settings.microsoft_client_id:
        raise RuntimeError(
            "YETI_MICROSOFT_CLIENT_ID must be set in .env"
        )

    return msal.PublicClientApplication(
        client_id=settings.microsoft_client_id,
        authority=_authority(),
        token_cache=cache,
    )


def run_oauth_flow(
    email: str, token_path: Path | None = None
) -> dict:
    """Run the interactive OAuth flow for one mailbox.

    Saves the serialized MSAL cache to `token_path` (default:
    `TOKEN_DIR/<slug>.json`). Returns the `id_token_claims` dict
    from the acquired token (useful to surface `tid`).
    """
    import msal

    if token_path is None:
        token_path = token_path_for(email)

    cache = msal.SerializableTokenCache()
    app = _build_msal_app(cache=cache)

    result = app.acquire_token_interactive(
        scopes=SCOPES,
        login_hint=email,
    )
    if "access_token" not in result:
        raise RuntimeError(
            f"Outlook auth failed: {result.get('error_description') or result}"
        )

    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(cache.serialize())
    return result.get("id_token_claims") or {}


def save_token_blob(email: str, token_json: str) -> Path:
    """Persist an uploaded MSAL cache blob for a mailbox."""
    tp = token_path_for(email)
    tp.parent.mkdir(parents=True, exist_ok=True)
    tp.write_text(token_json)
    return tp


def _load_cache(email: str):
    """Load the serialized MSAL cache for a mailbox."""
    import msal

    tp = token_path_for(email)
    if not tp.exists():
        return None
    cache = msal.SerializableTokenCache()
    cache.deserialize(tp.read_text())
    return cache


def load_access_token(email: str) -> tuple[str | None, str | None]:
    """Return (access_token, tid) for a mailbox, refreshing silently.

    tid is the tenant ID from the id_token_claims when available.
    """
    cache = _load_cache(email)
    if cache is None:
        return None, None

    app = _build_msal_app(cache=cache)
    accounts = app.get_accounts(username=email)
    if not accounts:
        # Fall back to any account in the cache.
        accounts = app.get_accounts()
    if not accounts:
        return None, None

    result = app.acquire_token_silent(SCOPES, account=accounts[0])
    if not result or "access_token" not in result:
        logger.warning(
            "Outlook silent refresh failed for %s: %s",
            email,
            result.get("error_description") if result else "no cache",
        )
        return None, None

    # Persist any refreshed cache state.
    if cache.has_state_changed:
        tp = token_path_for(email)
        tp.write_text(cache.serialize())

    claims = result.get("id_token_claims") or {}
    return result["access_token"], claims.get("tid")


def credential_status(email: str) -> dict:
    """Inspect token status for a mailbox (for API/status surfaces)."""
    tp = token_path_for(email)
    if not tp.exists():
        return {
            "configured": False,
            "reason": "No token. Run `yeti outlook-auth <email>`.",
        }
    access, tid = load_access_token(email)
    if not access:
        return {
            "configured": False,
            "reason": "Token invalid or could not refresh.",
        }
    return {
        "configured": True,
        "tid": tid or "",
        "has_refresh": True,
    }


class OutlookAdapter:
    """Microsoft Graph adapter — read inbox, save drafts, per mailbox."""

    def __init__(self, email: str):
        self.email = email

    def _headers(self) -> dict:
        access, _tid = load_access_token(self.email)
        if not access:
            raise RuntimeError(
                f"No valid Outlook credentials for {self.email}. "
                f"Run `yeti outlook-auth {self.email}` first."
            )
        return {
            "Authorization": f"Bearer {access}",
            "Accept": "application/json",
        }

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"{GRAPH_BASE}/me",
                    headers=self._headers(),
                )
                return r.is_success
        except Exception:
            logger.exception(
                "Outlook health check failed for %s", self.email
            )
            return False

    def list_messages_since(
        self,
        since: datetime,
        max_results: int = 100,
    ) -> list[dict[str, Any]]:
        """Fetch Inbox messages received after `since` (synchronous)."""
        iso = since.astimezone().isoformat(timespec="seconds")
        # Graph wants the `Z` form when UTC.
        if iso.endswith("+00:00"):
            iso = iso[:-6] + "Z"

        params = {
            "$filter": f"receivedDateTime ge {iso}",
            "$top": str(max_results),
            "$orderby": "receivedDateTime desc",
            "$select": (
                "id,conversationId,from,toRecipients,subject,"
                "receivedDateTime,bodyPreview,body,"
                "internetMessageHeaders"
            ),
        }

        url: str | None = f"{GRAPH_BASE}/me/mailFolders/Inbox/messages"
        next_params: dict | None = params
        messages: list[dict] = []
        try:
            with httpx.Client(timeout=30) as client:
                while url:
                    r = client.get(
                        url,
                        headers=self._headers(),
                        params=next_params,
                    )
                    r.raise_for_status()
                    payload = r.json()
                    for raw in payload.get("value", []):
                        messages.append(_parse_message(raw))
                    next_link = payload.get("@odata.nextLink")
                    if next_link and len(messages) < max_results:
                        url = next_link
                        # @odata.nextLink already carries query params.
                        next_params = None
                    else:
                        url = None
        except httpx.HTTPStatusError as e:
            logger.error(
                "Outlook list failed for %s: %s %s",
                self.email,
                e.response.status_code,
                e.response.text[:300],
            )
            raise
        return messages

    def save_draft(
        self,
        to: str,
        subject: str,
        body: str,
        in_reply_to: str | None = None,
    ) -> str:
        """Create a draft message. Returns the draft message id."""
        payload: dict = {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": [
                {"emailAddress": {"address": to}}
            ],
        }
        if in_reply_to:
            payload["internetMessageHeaders"] = [
                {
                    "name": "In-Reply-To",
                    "value": in_reply_to,
                },
                {
                    "name": "References",
                    "value": in_reply_to,
                },
            ]

        with httpx.Client(timeout=30) as client:
            r = client.post(
                f"{GRAPH_BASE}/me/messages",
                headers={
                    **self._headers(),
                    "Content-Type": "application/json",
                },
                content=json.dumps(payload),
            )
            r.raise_for_status()
            return r.json().get("id", "")


def _parse_message(raw: dict) -> dict:
    """Graph message -> flat dict matching Gmail's shape."""
    sender = raw.get("from", {}).get("emailAddress", {})
    from_str = (
        f'{sender.get("name", "")} <{sender.get("address", "")}>'
        if sender.get("address")
        else ""
    )

    to_list = []
    for r in raw.get("toRecipients") or []:
        addr = r.get("emailAddress", {})
        if addr.get("address"):
            to_list.append(addr["address"])
    to_str = ", ".join(to_list)

    body = raw.get("body") or {}
    body_text = _extract_body(body)

    headers = {
        h.get("name", ""): h.get("value", "")
        for h in raw.get("internetMessageHeaders") or []
        if h.get("name")
    }
    received_at = raw.get("receivedDateTime", "") or ""

    return {
        "id": raw.get("id", ""),
        "thread_id": raw.get("conversationId", ""),
        "from": from_str,
        "to": to_str,
        "subject": raw.get("subject", ""),
        "date": received_at,
        "received_at": received_at,
        "body": body_text,
        "headers": headers,
        "snippet": raw.get("bodyPreview", "") or "",
    }


def _extract_body(body: dict) -> str:
    """Extract text from a Graph message body, stripping HTML if needed."""
    content = body.get("content") or ""
    ctype = (body.get("contentType") or "").lower()
    if not content:
        return ""
    if ctype == "text":
        return content
    # HTML fallback — same stripper style as Gmail adapter.
    text = re.sub(r"<[^>]+>", " ", content)
    text = re.sub(r"\s+", " ", text)
    return text.strip()
