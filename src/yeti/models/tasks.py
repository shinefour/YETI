"""Tasks — units of work tracked by YETI.

A task is something that takes time to do, can be delegated, scheduled,
and has a lifecycle. Distinct from inbox items, which are fast-resolve
decisions handled in seconds.
"""

import enum
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from yeti.config import settings

DB_PATH = Path(settings.db_path)


class TaskStatus(enum.StrEnum):
    ACTIVE = "active"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class Task(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    source: str = ""
    status: TaskStatus = TaskStatus.ACTIVE
    assignee: str = ""
    due_date: str | None = None
    project: str = ""
    context: str = ""
    created_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )
    decided_at: str | None = None


class TaskStore:
    """SQLite-backed task storage."""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._migrate_from_actions()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    source TEXT DEFAULT '',
                    status TEXT DEFAULT 'active',
                    assignee TEXT DEFAULT '',
                    due_date TEXT,
                    project TEXT DEFAULT '',
                    context TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    decided_at TEXT
                )
            """)

    def _migrate_from_actions(self):
        """Copy old `actions` table data into `tasks` if needed."""
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='actions'"
            )
            if not cur.fetchone():
                return
            cur = conn.execute("SELECT COUNT(*) FROM tasks")
            if cur.fetchone()[0] > 0:
                return
            conn.execute(
                """
                INSERT INTO tasks
                SELECT * FROM actions
                """
            )

    def create(self, item: Task) -> Task:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO tasks
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

    def get(self, item_id: str) -> Task | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM tasks WHERE id = ?", (item_id,)
            ).fetchone()
        if not row:
            return None
        return Task(**dict(row))

    def list(
        self,
        status: TaskStatus | None = None,
        project: str | None = None,
    ) -> list[Task]:
        query = "SELECT * FROM tasks WHERE 1=1"
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
        return [Task(**dict(r)) for r in rows]

    def update_status(
        self, item_id: str, status: TaskStatus
    ) -> Task | None:
        decided = None
        if status in (
            TaskStatus.ACTIVE,
            TaskStatus.CANCELLED,
        ):
            decided = datetime.now(UTC).isoformat()
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE tasks
                SET status = ?, decided_at = COALESCE(?, decided_at)
                WHERE id = ?
                """,
                (status.value, decided, item_id),
            )
        return self.get(item_id)

    def delete(self, item_id: str) -> bool:
        with self._conn() as conn:
            cursor = conn.execute(
                "DELETE FROM tasks WHERE id = ?", (item_id,)
            )
        return cursor.rowcount > 0
