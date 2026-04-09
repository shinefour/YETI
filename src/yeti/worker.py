"""Celery worker configuration for background agents and scheduled jobs."""

from celery import Celery
from celery.schedules import crontab

from yeti.config import settings

celery_app = Celery("yeti", broker=settings.redis_url)

celery_app.conf.beat_schedule = {
    "morning-briefing": {
        "task": "yeti.worker.morning_briefing",
        "schedule": crontab(hour=7, minute=0),
    },
    "jira-sync": {
        "task": "yeti.worker.sync_jira",
        "schedule": 900.0,  # every 15 minutes
    },
    "notion-sync": {
        "task": "yeti.worker.sync_notion",
        "schedule": 300.0,  # every 5 minutes
    },
    "teams-digest": {
        "task": "yeti.worker.digest_teams",
        "schedule": 1800.0,  # every 30 minutes
    },
    "slack-digest": {
        "task": "yeti.worker.digest_slack",
        "schedule": 1800.0,  # every 30 minutes
    },
}


@celery_app.task
def morning_briefing():
    """Compile calendar, action items, updates and push to Telegram."""
    # TODO: Implement
    pass


@celery_app.task
def sync_jira():
    """Pull new/updated Jira issues into the knowledge base."""
    # TODO: Implement
    pass


@celery_app.task
def sync_notion():
    """Poll Notion for page changes and index into memory."""
    # TODO: Implement
    pass


@celery_app.task
def digest_teams():
    """Summarize unread Teams messages/mentions."""
    # TODO: Implement
    pass


@celery_app.task
def digest_slack():
    """Summarize unread Slack messages/mentions."""
    # TODO: Implement
    pass
