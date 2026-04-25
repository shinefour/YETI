"""Track Daniel's habitual dispositions on repeat inbox items.

Pattern-based prefill: when Daniel has resolved a "shape" of inbox
item the same way 2+ times, future occurrences arrive with that
disposition pre-selected — he confirms in one click. Autonomy is
opt-in per pattern (auto_apply flag); learning never silently changes
behaviour.

Pattern key shape: ``<type>::<title>`` (raw title for now). If false
positives surface later, normalise the title here without touching
the storage schema.
"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from yeti.config import settings

DB_PATH = Path(settings.db_path)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def make_pattern_key(item_type: str, title: str) -> str:
    return f"{item_type}::{(title or '').strip()}"


class ResolutionPatternStore:
    """SQLite-backed store for repeat-inbox-item dispositions."""

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
                CREATE TABLE IF NOT EXISTS resolution_patterns (
                    pattern_key TEXT PRIMARY KEY,
                    disposition TEXT NOT NULL,
                    count INTEGER NOT NULL DEFAULT 1,
                    last_seen TEXT NOT NULL,
                    auto_apply INTEGER NOT NULL DEFAULT 0
                )
            """)

    def get(self, pattern_key: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM resolution_patterns "
                "WHERE pattern_key = ?",
                (pattern_key,),
            ).fetchone()
        return dict(row) if row else None

    def record_resolution(
        self, pattern_key: str, disposition: str
    ) -> dict:
        """Upsert pattern: increment count if disposition matches,
        else reset count to 1 and store the new disposition.
        """
        existing = self.get(pattern_key)
        if existing and existing["disposition"] == disposition:
            new_count = int(existing["count"]) + 1
            with self._conn() as conn:
                conn.execute(
                    """
                    UPDATE resolution_patterns
                    SET count = ?, last_seen = ?
                    WHERE pattern_key = ?
                    """,
                    (new_count, _now(), pattern_key),
                )
        else:
            new_count = 1
            with self._conn() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO resolution_patterns
                        (pattern_key, disposition, count,
                         last_seen, auto_apply)
                    VALUES (?, ?, ?, ?, COALESCE(
                        (SELECT auto_apply FROM resolution_patterns
                         WHERE pattern_key = ?), 0))
                    """,
                    (
                        pattern_key,
                        disposition,
                        new_count,
                        _now(),
                        pattern_key,
                    ),
                )
        return {
            "pattern_key": pattern_key,
            "disposition": disposition,
            "count": new_count,
        }

    def suggestion_for(self, pattern_key: str) -> dict | None:
        """Return suggested disposition + count if confidence ≥ 2.

        Returns None when pattern is new or count < 2.
        """
        row = self.get(pattern_key)
        if not row or row["count"] < 2:
            return None
        return {
            "disposition": row["disposition"],
            "count": int(row["count"]),
            "auto_apply": bool(row["auto_apply"]),
        }

    def set_auto_apply(
        self, pattern_key: str, enabled: bool
    ) -> bool:
        """Flip the auto_apply flag. Returns True if pattern existed."""
        if not self.get(pattern_key):
            return False
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE resolution_patterns
                SET auto_apply = ?
                WHERE pattern_key = ?
                """,
                (1 if enabled else 0, pattern_key),
            )
        return True
