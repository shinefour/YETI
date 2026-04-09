"""Action items — the core unit of work tracked by YETI."""

import enum
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

DB_PATH = Path("data/yeti.db")


class ActionStatus(enum.StrEnum):
    PENDING_REVIEW = "pending_review"
    ACTIVE = "active"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ActionItem(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    source: str = ""
    status: ActionStatus = ActionStatus.PENDING_REVIEW
    assignee: str = ""
    due_date: str | None = None
    project: str = ""
    context: str = ""
    created_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )
    decided_at: str | None = None


class ActionStore:
    """SQLite-backed action item storage."""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS actions (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    source TEXT DEFAULT '',
                    status TEXT DEFAULT 'pending_review',
                    assignee TEXT DEFAULT '',
                    due_date TEXT,
                    project TEXT DEFAULT '',
                    context TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    decided_at TEXT
                )
            """)

    def create(self, item: ActionItem) -> ActionItem:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO actions
                    (id, title, source, status, assignee,
                     due_date, project, context,
                     created_at, decided_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.id,
                    item.title,
                    item.source,
                    item.status.value,
                    item.assignee,
                    item.due_date,
                    item.project,
                    item.context,
                    item.created_at,
                    item.decided_at,
                ),
            )
        return item

    def get(self, item_id: str) -> ActionItem | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM actions WHERE id = ?", (item_id,)
            ).fetchone()
        if not row:
            return None
        return ActionItem(**dict(row))

    def list(
        self,
        status: ActionStatus | None = None,
        project: str | None = None,
    ) -> list[ActionItem]:
        query = "SELECT * FROM actions WHERE 1=1"
        params: list = []
        if status:
            query += " AND status = ?"
            params.append(status.value)
        if project:
            query += " AND project = ?"
            params.append(project)
        query += " ORDER BY created_at DESC"
        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [ActionItem(**dict(r)) for r in rows]

    def update_status(
        self, item_id: str, status: ActionStatus
    ) -> ActionItem | None:
        decided = None
        if status in (
            ActionStatus.ACTIVE,
            ActionStatus.CANCELLED,
        ):
            decided = datetime.now(UTC).isoformat()
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE actions
                SET status = ?, decided_at = COALESCE(?, decided_at)
                WHERE id = ?
                """,
                (status.value, decided, item_id),
            )
        return self.get(item_id)

    def delete(self, item_id: str) -> bool:
        with self._conn() as conn:
            cursor = conn.execute(
                "DELETE FROM actions WHERE id = ?", (item_id,)
            )
        return cursor.rowcount > 0
