"""Celery worker — background agents and scheduled jobs."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from celery import Celery
from celery.schedules import crontab

from yeti.config import settings

logger = logging.getLogger(__name__)

celery_app = Celery("yeti", broker=settings.redis_url)

celery_app.conf.beat_schedule = {
    "morning-briefing": {
        "task": "yeti.worker.morning_briefing",
        "schedule": crontab(hour=7, minute=0),
    },
    "jira-sync": {
        "task": "yeti.worker.sync_jira",
        "schedule": 900.0,
    },
    "notion-sync": {
        "task": "yeti.worker.sync_notion",
        "schedule": 300.0,
    },
    "gmail-sync": {
        "task": "yeti.worker.sync_gmail",
        "schedule": 300.0,
    },
    "outlook-sync": {
        "task": "yeti.worker.sync_outlook",
        "schedule": 300.0,
    },
}


def _run_async(coro):
    """Run an async function from sync Celery task."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _send_telegram(message: str):
    """Send a message to Daniel via Telegram."""
    if not settings.telegram_bot_token:
        logger.warning("No Telegram token — skipping notification")
        return
    if not settings.telegram_allowed_chat_id:
        logger.warning("No Telegram chat ID — skipping notification")
        return

    import httpx

    url = (
        f"https://api.telegram.org/"
        f"bot{settings.telegram_bot_token}/sendMessage"
    )
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(
            url,
            json={
                "chat_id": settings.telegram_allowed_chat_id,
                "text": message,
                "parse_mode": "Markdown",
            },
        )


@celery_app.task
def morning_briefing():
    """Compile daily briefing and push to Telegram."""
    _run_async(_morning_briefing_async())


async def _morning_briefing_async():
    from yeti.models.tasks import TaskStatus, TaskStore

    lines = ["*YETI Morning Briefing*\n"]

    # Tasks summary (and inbox as new pending review)
    store = TaskStore()
    blocked = store.list(status=TaskStatus.BLOCKED)
    active = store.list(status=TaskStatus.ACTIVE)

    # Inbox count (replaces old pending_review concept)
    from yeti.models.inbox import InboxStore

    inbox_count = InboxStore().count_pending()
    if inbox_count:
        lines.append(f"*Inbox needs review:* {inbox_count}")

    if active:
        lines.append(f"\n*Active tasks:* {len(active)}")
        for item in active[:5]:
            lines.append(f"  - {item.title}")
    if blocked:
        lines.append(f"\n*Blocked:* {len(blocked)}")
        for item in blocked[:5]:
            lines.append(f"  - {item.title}")

    # Jira updates
    if settings.jira_url and settings.jira_api_token:
        try:
            from yeti.integrations.jira import JiraAdapter

            jira = JiraAdapter()
            since = datetime.now(UTC) - timedelta(hours=24)
            events = await jira.pull(since)
            if events:
                lines.append(f"\n*Jira updates:* {len(events)}")
                for e in events[:5]:
                    status = e.metadata.get("status", "")
                    lines.append(f"  - {e.title} [{status}]")
        except Exception:
            logger.exception("Jira pull failed in briefing")

    if not blocked and not active and not inbox_count:
        lines.append("No tasks. Clean slate.")

    await _send_telegram("\n".join(lines))


@celery_app.task
def sync_jira():
    """Pull recent Jira updates."""
    if not settings.jira_url or not settings.jira_api_token:
        return
    _run_async(_sync_jira_async())


async def _sync_jira_async():
    from yeti.integrations.jira import JiraAdapter

    jira = JiraAdapter()
    since = datetime.now(UTC) - timedelta(minutes=15)
    try:
        events = await jira.pull(since)
        logger.info("Jira sync: %d events", len(events))
        # TODO: store events in MemPalace once connected
    except Exception:
        logger.exception("Jira sync failed")


@celery_app.task
def sync_notion():
    """Poll Notion for page changes."""
    if not settings.notion_api_key:
        return
    _run_async(_sync_notion_async())


async def _sync_notion_async():
    from yeti.integrations.notion import NotionAdapter

    notion = NotionAdapter()
    since = datetime.now(UTC) - timedelta(minutes=5)
    try:
        events = await notion.pull(since)
        logger.info("Notion sync: %d events", len(events))
        # TODO: store events in MemPalace once connected
    except Exception:
        logger.exception("Notion sync failed")


@celery_app.task
def sync_gmail():
    """Pull new Gmail messages and queue them for triage."""
    if not settings.gmail_client_id:
        return
    _sync_gmail_sync()


def _sync_gmail_sync():
    """Sync Gmail (runs in worker thread, no async needed).

    Uses date-based watermark sync. Never modifies the mailbox.
    First sync backfills the last 24 hours.
    """
    from yeti.email.filters import filter_email
    from yeti.integrations.gmail import GmailAdapter
    from yeti.models.notes import Note, NoteSource, NoteStore
    from yeti.models.sync_state import SyncStateStore

    mailbox = "gmail"
    state = SyncStateStore()

    since = state.get_watermark(mailbox)
    if since is None:
        since = datetime.now(UTC) - timedelta(hours=24)
        logger.info(
            "Gmail first sync: backfilling last 24h"
        )

    try:
        adapter = GmailAdapter()
        messages = adapter.list_messages_since(
            since, max_results=100
        )
    except RuntimeError as e:
        logger.warning("Gmail sync skipped: %s", e)
        return
    except Exception:
        logger.exception("Gmail sync failed")
        return

    if not messages:
        return

    note_store = NoteStore()
    ingested = 0
    skipped = 0
    deduped = 0
    newest_ts = since
    for msg in messages:
        msg_id = msg["id"]

        # Dedup against previously seen messages
        if state.already_seen(mailbox, msg_id):
            deduped += 1
            continue

        # Track newest timestamp seen this poll
        try:
            received = datetime.fromisoformat(
                msg.get("received_at") or ""
            )
            if received > newest_ts:
                newest_ts = received
        except (ValueError, TypeError):
            pass

        sender = msg.get("from", "")
        should_ingest, reason = filter_email(
            sender, msg.get("headers", {})
        )

        # Mark as seen regardless (filtered or ingested) so
        # we don't re-evaluate noise on every poll
        state.mark_seen(mailbox, msg_id)

        if not should_ingest:
            logger.info(
                "Skipped email from %s: %s",
                sender,
                reason,
            )
            skipped += 1
            continue

        # Build the note content
        title = msg.get("subject", "(no subject)")
        content = (
            f"From: {sender}\n"
            f"To: {msg.get('to', '')}\n"
            f"Subject: {title}\n"
            f"Date: {msg.get('date', '')}\n"
            f"\n"
            f"{msg.get('body', '') or msg.get('snippet', '')}"
        )

        forced_wing = settings.gmail_wing()
        wing_hint = (
            f" Wing: {forced_wing}. "
            "(Strictly scoped — do not cross-route.)"
        )
        note = note_store.create(
            Note(
                content=content,
                title=title,
                context=(
                    f"Email received via Gmail mailbox "
                    f"({settings.gmail_email})."
                    f"{wing_hint}"
                ),
                source=NoteSource.EMAIL,
                forced_wing=forced_wing,
            )
        )
        triage_note.delay(note.id)
        ingested += 1

    # Advance watermark to the newest message we've seen
    if newest_ts > since:
        state.set_watermark(mailbox, newest_ts)

    logger.info(
        "Gmail sync: ingested=%d skipped=%d deduped=%d",
        ingested,
        skipped,
        deduped,
    )


@celery_app.task
def sync_outlook():
    """Pull new Outlook messages for every configured mailbox."""
    if not settings.microsoft_client_id:
        return
    mailbox_map = settings.outlook_mailbox_map()
    if not mailbox_map:
        return
    for email, wing in mailbox_map.items():
        try:
            _sync_outlook_one(email, wing)
        except Exception:
            logger.exception(
                "Outlook sync failed for %s", email
            )


def _sync_outlook_one(email: str, wing: str):
    """Sync a single Outlook mailbox into its pinned wing.

    Per-mailbox watermark + dedup via SyncStateStore. Never modifies
    the mailbox. First sync backfills the last 24 hours.
    """
    from yeti.email.filters import filter_email
    from yeti.integrations.outlook import OutlookAdapter
    from yeti.models.notes import Note, NoteSource, NoteStore
    from yeti.models.sync_state import SyncStateStore

    mailbox = f"outlook:{email}"
    state = SyncStateStore()

    since = state.get_watermark(mailbox)
    if since is None:
        since = datetime.now(UTC) - timedelta(hours=24)
        logger.info(
            "Outlook first sync for %s: backfilling last 24h",
            email,
        )

    try:
        adapter = OutlookAdapter(email)
        messages = adapter.list_messages_since(
            since, max_results=100
        )
    except RuntimeError as e:
        logger.warning(
            "Outlook sync skipped for %s: %s", email, e
        )
        return
    except Exception:
        logger.exception(
            "Outlook sync failed for %s", email
        )
        return

    if not messages:
        return

    note_store = NoteStore()
    ingested = 0
    skipped = 0
    deduped = 0
    newest_ts = since
    for msg in messages:
        msg_id = msg["id"]

        if state.already_seen(mailbox, msg_id):
            deduped += 1
            continue

        try:
            received = datetime.fromisoformat(
                (msg.get("received_at") or "").replace(
                    "Z", "+00:00"
                )
            )
            if received > newest_ts:
                newest_ts = received
        except (ValueError, TypeError):
            pass

        sender = msg.get("from", "")
        should_ingest, reason = filter_email(
            sender, msg.get("headers", {})
        )

        state.mark_seen(mailbox, msg_id)

        if not should_ingest:
            logger.info(
                "Skipped email from %s (%s): %s",
                sender,
                email,
                reason,
            )
            skipped += 1
            continue

        title = msg.get("subject", "(no subject)")
        content = (
            f"From: {sender}\n"
            f"To: {msg.get('to', '')}\n"
            f"Subject: {title}\n"
            f"Date: {msg.get('date', '')}\n"
            f"\n"
            f"{msg.get('body', '') or msg.get('snippet', '')}"
        )

        note = note_store.create(
            Note(
                content=content,
                title=title,
                context=(
                    f"Email received via Outlook mailbox "
                    f"({email}). Wing: {wing}. "
                    f"(Strictly scoped — do not cross-route.)"
                ),
                source=NoteSource.EMAIL,
                forced_wing=wing,
            )
        )
        triage_note.delay(note.id)
        ingested += 1

    if newest_ts > since:
        state.set_watermark(mailbox, newest_ts)

    logger.info(
        "Outlook sync %s: ingested=%d skipped=%d deduped=%d",
        email,
        ingested,
        skipped,
        deduped,
    )


@celery_app.task
def triage_note(note_id: str):
    """Process a note via the Triage Agent."""
    _run_async(_triage_note_async(note_id))


async def _triage_note_async(note_id: str):
    from yeti.agents.triage import triage_note_content
    from yeti.models.notes import NoteStore

    store = NoteStore()
    note = store.get(note_id)
    if not note:
        logger.error("Triage: note %s not found", note_id)
        return

    store.mark_processing(note_id)
    try:
        summary = await triage_note_content(note)
        store.mark_processed(note_id, summary=summary)
        await _send_telegram(
            f"Note triaged: {summary[:200]}"
        )
    except Exception as e:
        logger.exception("Triage failed for %s", note_id)
        store.mark_failed(note_id, str(e))
