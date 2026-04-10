"""Inbox — fast-resolve items requiring Daniel's attention.

An inbox item represents a single decision (approve/edit/reject/pick)
that should take seconds. Anything that requires real work is converted
to a Task.
"""

import enum
import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from yeti.config import settings

DB_PATH = Path(settings.db_path)


class InboxType(enum.StrEnum):
    DECISION = "decision"
    DISAMBIGUATION = "disambiguation"
    PROPOSED_ACTION = "proposed_action"
    PERSON_UPDATE = "person_update"
    NOTIFICATION = "notification"


class InboxStatus(enum.StrEnum):
    PENDING = "pending"
    RESOLVED = "resolved"


class InboxItem(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: InboxType
    title: str
    summary: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    source: str = ""
    confidence: float = 1.0
    status: InboxStatus = InboxStatus.PENDING
    created_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )
    resolved_at: str | None = None
    resolution: str = ""  # approved/rejected/edited/picked/etc
    resolution_note: str = ""


class InboxAuditEntry(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    item_id: str
    timestamp: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )
    action: str  # created/resolved/converted_to_task
    details: dict[str, Any] = Field(default_factory=dict)


class InboxStore:
    """SQLite-backed inbox storage with audit log."""

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
                CREATE TABLE IF NOT EXISTS inbox (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT DEFAULT '',
                    payload TEXT DEFAULT '{}',
                    source TEXT DEFAULT '',
                    confidence REAL DEFAULT 1.0,
                    status TEXT DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    resolved_at TEXT,
                    resolution TEXT DEFAULT '',
                    resolution_note TEXT DEFAULT ''
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_inbox_status
                ON inbox(status)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_inbox_created
                ON inbox(created_at)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS inbox_audit (
                    id TEXT PRIMARY KEY,
                    item_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    action TEXT NOT NULL,
                    details TEXT DEFAULT '{}'
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_item
                ON inbox_audit(item_id)
            """)

    def create(self, item: InboxItem) -> InboxItem:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO inbox
                    (id, type, title, summary, payload, source,
                     confidence, status, created_at, resolved_at,
                     resolution, resolution_note)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.id,
                    item.type.value,
                    item.title,
                    item.summary,
                    json.dumps(item.payload),
                    item.source,
                    item.confidence,
                    item.status.value,
                    item.created_at,
                    item.resolved_at,
                    item.resolution,
                    item.resolution_note,
                ),
            )
        self._audit(item.id, "created", {"type": item.type.value})
        return item

    def _row_to_item(self, row: sqlite3.Row) -> InboxItem:
        data = dict(row)
        try:
            data["payload"] = json.loads(data["payload"] or "{}")
        except json.JSONDecodeError:
            data["payload"] = {}
        return InboxItem(**data)

    def get(self, item_id: str) -> InboxItem | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM inbox WHERE id = ?",
                (item_id,),
            ).fetchone()
        return self._row_to_item(row) if row else None

    def list_pending(self) -> list[InboxItem]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM inbox
                WHERE status = 'pending'
                ORDER BY created_at ASC
                """
            ).fetchall()
        return [self._row_to_item(r) for r in rows]

    def count_pending(self) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM inbox WHERE status = 'pending'"
            ).fetchone()
        return row[0] if row else 0

    def resolve(
        self,
        item_id: str,
        resolution: str,
        note: str = "",
    ) -> InboxItem | None:
        now = datetime.now(UTC).isoformat()
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE inbox
                SET status = 'resolved',
                    resolved_at = ?,
                    resolution = ?,
                    resolution_note = ?
                WHERE id = ?
                """,
                (now, resolution, note, item_id),
            )
        self._audit(
            item_id,
            "resolved",
            {"resolution": resolution, "note": note},
        )
        return self.get(item_id)

    def _audit(
        self,
        item_id: str,
        action: str,
        details: dict[str, Any],
    ) -> None:
        entry = InboxAuditEntry(
            item_id=item_id,
            action=action,
            details=details,
        )
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO inbox_audit
                    (id, item_id, timestamp, action, details)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    entry.id,
                    entry.item_id,
                    entry.timestamp,
                    entry.action,
                    json.dumps(entry.details),
                ),
            )

    def audit_log(
        self, item_id: str | None = None, limit: int = 100
    ) -> list[InboxAuditEntry]:
        query = "SELECT * FROM inbox_audit"
        params: list = []
        if item_id:
            query += " WHERE item_id = ?"
            params.append(item_id)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
        result = []
        for r in rows:
            data = dict(r)
            try:
                data["details"] = json.loads(
                    data["details"] or "{}"
                )
            except json.JSONDecodeError:
                data["details"] = {}
            result.append(InboxAuditEntry(**data))
        return result
