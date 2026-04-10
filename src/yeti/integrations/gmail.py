"""Gmail integration via OAuth2 + Gmail API.

Uses the standard google-auth flow. Token persisted to disk.

Setup:
  1. Create OAuth client (Desktop app) in Google Cloud Console
  2. Set YETI_GMAIL_CLIENT_ID and YETI_GMAIL_CLIENT_SECRET
  3. Run `yeti gmail-auth` once to grant access (saves token)
  4. Sync runs automatically via Celery
"""

import base64
import json
import logging
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

from yeti.config import settings

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
]

TOKEN_PATH = Path("/data/yeti/gmail_token.json")


def _client_config() -> dict:
    """Build the OAuth client config dict from settings."""
    return {
        "installed": {
            "client_id": settings.gmail_client_id,
            "client_secret": settings.gmail_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }


def run_oauth_flow(token_path: Path = TOKEN_PATH) -> str:
    """Run the local OAuth flow once. Saves the token to disk.

    Must be called from a machine with a browser. The resulting
    token can then be uploaded to the production server.
    """
    from google_auth_oauthlib.flow import InstalledAppFlow

    if (
        not settings.gmail_client_id
        or not settings.gmail_client_secret
    ):
        raise RuntimeError(
            "YETI_GMAIL_CLIENT_ID and YETI_GMAIL_CLIENT_SECRET "
            "must be set in .env"
        )

    flow = InstalledAppFlow.from_client_config(
        _client_config(), SCOPES
    )
    creds = flow.run_local_server(port=0)

    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_data = json.loads(creds.to_json())
    token_path.write_text(json.dumps(token_data, indent=2))
    return str(token_path)


def load_credentials(token_path: Path = TOKEN_PATH):
    """Load saved credentials, refreshing if needed."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not token_path.exists():
        return None

    creds = Credentials.from_authorized_user_file(
        str(token_path), SCOPES
    )

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            token_path.write_text(creds.to_json())
        except Exception:
            logger.exception("Failed to refresh Gmail token")
            return None

    return creds


def save_token_blob(token_json: str) -> None:
    """Save a token JSON blob received via API upload."""
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(token_json)


class GmailAdapter:
    """Gmail API adapter — read messages, save drafts."""

    def __init__(self):
        self._service = None

    def _get_service(self):
        if self._service is not None:
            return self._service

        from googleapiclient.discovery import build

        creds = load_credentials()
        if not creds:
            raise RuntimeError(
                "No valid Gmail credentials. "
                "Run `yeti gmail-auth` first."
            )
        self._service = build(
            "gmail", "v1", credentials=creds, cache_discovery=False
        )
        return self._service

    async def health(self) -> bool:
        if not settings.gmail_client_id:
            return False
        try:
            svc = self._get_service()
            svc.users().getProfile(userId="me").execute()
            return True
        except Exception:
            logger.exception("Gmail health check failed")
            return False

    def list_messages_since(
        self,
        since: datetime,
        max_results: int = 100,
    ) -> list[dict[str, Any]]:
        """Fetch INBOX messages received after the given timestamp."""
        svc = self._get_service()

        # Gmail's `after:` query takes a Unix timestamp (seconds)
        unix_ts = int(since.timestamp())
        query = f"in:inbox after:{unix_ts}"
        result = (
            svc.users()
            .messages()
            .list(
                userId="me",
                q=query,
                maxResults=max_results,
            )
            .execute()
        )

        message_refs = result.get("messages", [])
        messages = []
        for ref in message_refs:
            try:
                full = (
                    svc.users()
                    .messages()
                    .get(
                        userId="me",
                        id=ref["id"],
                        format="full",
                    )
                    .execute()
                )
                messages.append(_parse_message(full))
            except Exception:
                logger.exception(
                    "Failed to fetch message %s", ref["id"]
                )
        return messages

    def save_draft(
        self,
        to: str,
        subject: str,
        body: str,
        in_reply_to: str | None = None,
    ) -> str:
        """Save a draft message. Returns the draft ID."""
        svc = self._get_service()

        message = MIMEText(body)
        message["to"] = to
        message["from"] = settings.gmail_email
        message["subject"] = subject
        if in_reply_to:
            message["In-Reply-To"] = in_reply_to
            message["References"] = in_reply_to

        raw = base64.urlsafe_b64encode(
            message.as_bytes()
        ).decode()

        draft = (
            svc.users()
            .drafts()
            .create(
                userId="me",
                body={"message": {"raw": raw}},
            )
            .execute()
        )
        return draft["id"]


def _parse_message(message: dict) -> dict:
    """Parse a Gmail API message into a flat dict."""
    headers = {
        h["name"]: h["value"]
        for h in message.get("payload", {}).get("headers", [])
    }
    body = _extract_body(message.get("payload", {}))

    internal_date_ms = int(message.get("internalDate", "0"))
    received_at = (
        datetime.fromtimestamp(
            internal_date_ms / 1000
        ).isoformat()
        if internal_date_ms
        else ""
    )

    return {
        "id": message["id"],
        "thread_id": message.get("threadId", ""),
        "from": headers.get("From", ""),
        "to": headers.get("To", ""),
        "subject": headers.get("Subject", ""),
        "date": headers.get("Date", ""),
        "received_at": received_at,
        "body": body,
        "headers": headers,
        "snippet": message.get("snippet", ""),
    }


def _extract_body(payload: dict) -> str:
    """Recursively find the text/plain body."""
    if not payload:
        return ""

    mime_type = payload.get("mimeType", "")
    body_data = payload.get("body", {}).get("data", "")

    if mime_type == "text/plain" and body_data:
        try:
            return base64.urlsafe_b64decode(
                body_data + "=" * (4 - len(body_data) % 4)
            ).decode("utf-8", errors="replace")
        except Exception:
            return ""

    for part in payload.get("parts", []):
        result = _extract_body(part)
        if result:
            return result

    # Fallback to html
    if mime_type == "text/html" and body_data:
        try:
            html = base64.urlsafe_b64decode(
                body_data + "=" * (4 - len(body_data) % 4)
            ).decode("utf-8", errors="replace")
            # Strip basic HTML
            import re

            text = re.sub(r"<[^>]+>", " ", html)
            text = re.sub(r"\s+", " ", text)
            return text.strip()
        except Exception:
            return ""

    return ""
