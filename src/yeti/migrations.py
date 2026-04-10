"""One-shot migrations run on app startup."""

import logging
import sqlite3
from pathlib import Path

from yeti.config import settings

logger = logging.getLogger(__name__)


def run_all() -> None:
    """Run all pending migrations. Idempotent."""
    db_path = Path(settings.db_path)
    if not db_path.exists():
        return

    _migrate_pending_tasks_to_inbox(db_path)


def _migrate_pending_tasks_to_inbox(db_path: Path) -> None:
    """Convert any pending_review tasks into PROPOSED_ACTION inbox items."""
    from yeti.models.inbox import (
        InboxItem,
        InboxStore,
        InboxType,
    )

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='tasks'"
        )
        if not cur.fetchone():
            return

        rows = conn.execute(
            "SELECT * FROM tasks WHERE status = 'pending_review'"
        ).fetchall()

        if not rows:
            return

        logger.info(
            "Migrating %d pending_review tasks to inbox", len(rows)
        )

        inbox = InboxStore()
        for row in rows:
            row_dict = dict(row)
            inbox.create(
                InboxItem(
                    type=InboxType.PROPOSED_ACTION,
                    title=row_dict["title"],
                    summary=(
                        row_dict.get("context", "")
                        or "Proposed task from triage"
                    ),
                    answer_schema=[
                        {
                            "key": "title",
                            "label": "Task title",
                            "type": "text",
                            "value": row_dict["title"],
                        },
                        {
                            "key": "assignee",
                            "label": "Assignee",
                            "type": "text",
                            "value": row_dict.get(
                                "assignee", ""
                            )
                            or "Daniel",
                        },
                        {
                            "key": "due_date",
                            "label": "Due date (YYYY-MM-DD)",
                            "type": "text",
                            "value": row_dict.get("due_date") or "",
                        },
                        {
                            "key": "project",
                            "label": "Project",
                            "type": "text",
                            "value": row_dict.get(
                                "project", ""
                            )
                            or "",
                        },
                    ],
                    quick_actions=["discard"],
                    payload={
                        "original_task_id": row_dict["id"],
                        "context": row_dict.get(
                            "context", ""
                        ),
                    },
                    source=row_dict.get(
                        "source", "migration"
                    ),
                )
            )

        # Mark migrated tasks as cancelled so they don't show again
        conn.execute(
            "UPDATE tasks SET status = 'cancelled' "
            "WHERE status = 'pending_review'"
        )
        conn.commit()
        logger.info("Migration complete")
    except Exception:
        logger.exception("Task migration failed")
    finally:
        conn.close()
