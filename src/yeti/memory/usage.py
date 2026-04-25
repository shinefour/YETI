"""Retrieval log — what's actually being read from MemPalace.

Sleep / pruning operations need to know which drawers and KG entities
have been queried recently. Without this, every "is this still useful?"
decision is guesswork. Logging is fire-and-forget — failures here must
never block a retrieval.
"""

import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from yeti.config import settings

logger = logging.getLogger(__name__)
DB_PATH = Path(settings.db_path)


class UsageStore:
    """SQLite-backed retrieval log."""

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
                CREATE TABLE IF NOT EXISTS retrieval_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    drawer_id TEXT,
                    fact_subject TEXT,
                    query TEXT,
                    source TEXT NOT NULL,
                    ts TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_retrieval_drawer
                ON retrieval_log(drawer_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_retrieval_ts
                ON retrieval_log(ts)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_retrieval_subject
                ON retrieval_log(fact_subject)
            """)

    def _now(self) -> str:
        return datetime.now(UTC).isoformat()

    def log_search(self, query: str, source: str) -> None:
        """Log a drawer search call (drawer ids unknown)."""
        if not query:
            return
        try:
            with self._conn() as conn:
                conn.execute(
                    """
                    INSERT INTO retrieval_log
                        (query, source, ts) VALUES (?, ?, ?)
                    """,
                    (query, source, self._now()),
                )
        except Exception:
            logger.exception("Failed to log search")

    def log_drawer_hits(
        self,
        drawer_ids: list[str],
        source: str,
        query: str | None = None,
    ) -> None:
        """Log specific drawer ids that were retrieved."""
        clean = [d for d in drawer_ids if d]
        if not clean:
            return
        now = self._now()
        try:
            with self._conn() as conn:
                conn.executemany(
                    """
                    INSERT INTO retrieval_log
                        (drawer_id, query, source, ts)
                    VALUES (?, ?, ?, ?)
                    """,
                    [(d, query, source, now) for d in clean],
                )
        except Exception:
            logger.exception("Failed to log drawer hits")

    def log_kg_query(self, entity: str, source: str) -> None:
        """Log a knowledge-graph entity lookup."""
        if not entity:
            return
        try:
            with self._conn() as conn:
                conn.execute(
                    """
                    INSERT INTO retrieval_log
                        (fact_subject, source, ts) VALUES (?, ?, ?)
                    """,
                    (entity, source, self._now()),
                )
        except Exception:
            logger.exception("Failed to log kg query")

    def drawer_hit_count(
        self,
        drawer_id: str,
        since: datetime | None = None,
    ) -> int:
        """How many times this drawer has been retrieved (optionally since)."""
        if not drawer_id:
            return 0
        if since is None:
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) FROM retrieval_log "
                    "WHERE drawer_id = ?",
                    (drawer_id,),
                ).fetchone()
        else:
            with self._conn() as conn:
                row = conn.execute(
                    """
                    SELECT COUNT(*) FROM retrieval_log
                    WHERE drawer_id = ? AND ts >= ?
                    """,
                    (drawer_id, since.isoformat()),
                ).fetchone()
        return row[0] if row else 0

    def last_retrieved_for_entity(self, entity: str) -> str | None:
        """Most recent ts an entity was queried, ISO8601 string."""
        if not entity:
            return None
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT MAX(ts) FROM retrieval_log
                WHERE fact_subject = ?
                """,
                (entity,),
            ).fetchone()
        return row[0] if row and row[0] else None

    def entity_hit_count(
        self,
        entity: str,
        since: datetime | None = None,
    ) -> int:
        """How many times an entity has been queried (optionally since)."""
        if not entity:
            return 0
        if since is None:
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) FROM retrieval_log "
                    "WHERE fact_subject = ?",
                    (entity,),
                ).fetchone()
        else:
            with self._conn() as conn:
                row = conn.execute(
                    """
                    SELECT COUNT(*) FROM retrieval_log
                    WHERE fact_subject = ? AND ts >= ?
                    """,
                    (entity, since.isoformat()),
                ).fetchone()
        return row[0] if row else 0
