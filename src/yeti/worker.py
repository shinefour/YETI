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

    # Action items summary
    store = TaskStore()
    pending = store.list(status=TaskStatus.PENDING_REVIEW)
    active = store.list(status=TaskStatus.ACTIVE)

    if pending:
        lines.append(f"*Pending review:* {len(pending)}")
        for item in pending[:5]:
            lines.append(f"  - {item.title}")
    if active:
        lines.append(f"\n*Active items:* {len(active)}")
        for item in active[:5]:
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

    if not pending and not active:
        lines.append("No action items. Clean slate.")

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
