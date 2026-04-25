"""Track drawers that have been deduplicated / replaced.

Sleep operations don't delete drawers immediately — too risky if a
sweep produces a false positive. Instead we mark the older drawer as
superseded, point at the canonical id, and have search consumers
filter out superseded ids. Removal can be a later, manual step once
trust is established.
"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from yeti.config import settings

DB_PATH = Path(settings.db_path)


class SupersededStore:
    """SQLite-backed supersession tracker for drawer ids."""

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
                CREATE TABLE IF NOT EXISTS superseded_drawers (
                    drawer_id TEXT PRIMARY KEY,
                    superseded_by TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    ts TEXT NOT NULL
                )
            """)

    def supersede(
        self,
        drawer_id: str,
        superseded_by: str,
        reason: str,
    ) -> None:
        if not drawer_id or not superseded_by:
            return
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO superseded_drawers
                    (drawer_id, superseded_by, reason, ts)
                VALUES (?, ?, ?, ?)
                """,
                (
                    drawer_id,
                    superseded_by,
                    reason,
                    datetime.now(UTC).isoformat(),
                ),
            )

    def is_superseded(self, drawer_id: str) -> bool:
        if not drawer_id:
            return False
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM superseded_drawers "
                "WHERE drawer_id = ?",
                (drawer_id,),
            ).fetchone()
        return row is not None

    def superseded_ids(self) -> set[str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT drawer_id FROM superseded_drawers"
            ).fetchall()
        return {r["drawer_id"] for r in rows}

    def count(self) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM superseded_drawers"
            ).fetchone()
        return row[0] if row else 0
