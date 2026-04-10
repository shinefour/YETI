"""Per-mailbox sync watermark and message dedup."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from yeti.config import settings

DB_PATH = Path(settings.db_path)


class SyncStateStore:
    """Tracks last-synced timestamp and seen message IDs per mailbox."""

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
                CREATE TABLE IF NOT EXISTS sync_watermark (
                    mailbox TEXT PRIMARY KEY,
                    last_synced_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sync_seen (
                    mailbox TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    ingested_at TEXT NOT NULL,
                    PRIMARY KEY (mailbox, message_id)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_sync_seen_mailbox
                ON sync_seen(mailbox)
            """)

    def get_watermark(self, mailbox: str) -> datetime | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT last_synced_at FROM sync_watermark "
                "WHERE mailbox = ?",
                (mailbox,),
            ).fetchone()
        if not row:
            return None
        return datetime.fromisoformat(row["last_synced_at"])

    def set_watermark(
        self, mailbox: str, ts: datetime
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO sync_watermark
                    (mailbox, last_synced_at) VALUES (?, ?)
                """,
                (mailbox, ts.isoformat()),
            )

    def mark_seen(
        self, mailbox: str, message_id: str
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO sync_seen
                    (mailbox, message_id, ingested_at)
                VALUES (?, ?, ?)
                """,
                (
                    mailbox,
                    message_id,
                    datetime.now(UTC).isoformat(),
                ),
            )

    def already_seen(
        self, mailbox: str, message_id: str
    ) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM sync_seen "
                "WHERE mailbox = ? AND message_id = ?",
                (mailbox, message_id),
            ).fetchone()
        return row is not None
