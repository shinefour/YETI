"""Notes — raw text captured for later triage.

A note is a freeform input (meeting notes, email body, idea, etc.)
that gets stored verbatim and processed asynchronously by the
Triage Agent to extract entities, facts, and action items.
"""

import enum
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from yeti.config import settings

DB_PATH = Path(settings.db_path)


class NoteSource(enum.StrEnum):
    TELEGRAM = "telegram"
    DASHBOARD = "dashboard"
    CLI = "cli"
    EMAIL = "email"
    API = "api"


class NoteStatus(enum.StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"


class Note(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    content: str
    source: NoteSource = NoteSource.API
    title: str = ""
    context: str = ""  # optional caption / additional context
    status: NoteStatus = NoteStatus.PENDING
    created_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )
    processed_at: str | None = None
    triage_summary: str = ""
    error: str = ""


class NoteStore:
    """SQLite-backed note storage."""

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
                CREATE TABLE IF NOT EXISTS notes (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    source TEXT DEFAULT 'api',
                    title TEXT DEFAULT '',
                    context TEXT DEFAULT '',
                    status TEXT DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    processed_at TEXT,
                    triage_summary TEXT DEFAULT '',
                    error TEXT DEFAULT ''
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_notes_status
                ON notes(status)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_notes_created
                ON notes(created_at)
            """)

    def create(self, note: Note) -> Note:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO notes
                    (id, content, source, title, context,
                     status, created_at, processed_at,
                     triage_summary, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    note.id,
                    note.content,
                    note.source.value,
                    note.title,
                    note.context,
                    note.status.value,
                    note.created_at,
                    note.processed_at,
                    note.triage_summary,
                    note.error,
                ),
            )
        return note

    def get(self, note_id: str) -> Note | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM notes WHERE id = ?",
                (note_id,),
            ).fetchone()
        return Note(**dict(row)) if row else None

    def list_by_status(
        self, status: NoteStatus, limit: int = 100
    ) -> list[Note]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM notes
                WHERE status = ?
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (status.value, limit),
            ).fetchall()
        return [Note(**dict(r)) for r in rows]

    def recent(self, limit: int = 50) -> list[Note]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM notes
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [Note(**dict(r)) for r in rows]

    def mark_processing(self, note_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE notes SET status = 'processing' WHERE id = ?",
                (note_id,),
            )

    def mark_processed(
        self, note_id: str, summary: str = ""
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE notes
                SET status = 'processed',
                    processed_at = ?,
                    triage_summary = ?
                WHERE id = ?
                """,
                (now, summary, note_id),
            )

    def mark_failed(self, note_id: str, error: str) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE notes
                SET status = 'failed', error = ?
                WHERE id = ?
                """,
                (error, note_id),
            )
