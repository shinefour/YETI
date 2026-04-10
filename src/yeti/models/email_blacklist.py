"""Email sender blacklist — patterns that should never be ingested."""

import fnmatch
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from yeti.config import settings

DB_PATH = Path(settings.db_path)


class BlacklistEntry(BaseModel):
    pattern: str  # exact email or wildcard like noreply@*
    reason: str = ""
    added_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )


class EmailBlacklistStore:
    """SQLite-backed sender blacklist with wildcard support."""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._cache: list[BlacklistEntry] | None = None

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS email_blacklist (
                    pattern TEXT PRIMARY KEY,
                    reason TEXT DEFAULT '',
                    added_at TEXT NOT NULL
                )
            """)

    def add(self, pattern: str, reason: str = "") -> BlacklistEntry:
        entry = BlacklistEntry(
            pattern=pattern.lower().strip(), reason=reason
        )
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO email_blacklist "
                "(pattern, reason, added_at) VALUES (?, ?, ?)",
                (entry.pattern, entry.reason, entry.added_at),
            )
        self._cache = None
        return entry

    def remove(self, pattern: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM email_blacklist WHERE pattern = ?",
                (pattern.lower().strip(),),
            )
        self._cache = None
        return cur.rowcount > 0

    def list_all(self) -> list[BlacklistEntry]:
        if self._cache is None:
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT * FROM email_blacklist "
                    "ORDER BY added_at DESC"
                ).fetchall()
            self._cache = [
                BlacklistEntry(**dict(r)) for r in rows
            ]
        return self._cache

    def matches(self, sender_email: str) -> str | None:
        """Return the matching pattern if blacklisted, else None."""
        if not sender_email:
            return None
        sender = sender_email.lower().strip()
        for entry in self.list_all():
            if fnmatch.fnmatch(sender, entry.pattern):
                return entry.pattern
        return None
