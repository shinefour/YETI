"""Model usage tracking — log every LLM call with cost and token info."""

import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

DB_PATH = Path("data/yeti.db")


class UsageRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )
    model: str
    provider: str = ""
    agent: str = ""
    task_type: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    request_summary: str = ""
    fallback_from: str = ""


class UsageStore:
    """SQLite-backed model usage tracking."""

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
                CREATE TABLE IF NOT EXISTS model_usage (
                    id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    model TEXT NOT NULL,
                    provider TEXT DEFAULT '',
                    agent TEXT DEFAULT '',
                    task_type TEXT DEFAULT '',
                    tokens_in INTEGER DEFAULT 0,
                    tokens_out INTEGER DEFAULT 0,
                    cost_usd REAL DEFAULT 0.0,
                    request_summary TEXT DEFAULT '',
                    fallback_from TEXT DEFAULT ''
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_usage_timestamp
                ON model_usage(timestamp)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_usage_model
                ON model_usage(model)
            """)

    def record(self, item: UsageRecord) -> UsageRecord:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO model_usage
                    (id, timestamp, model, provider, agent,
                     task_type, tokens_in, tokens_out, cost_usd,
                     request_summary, fallback_from)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.id,
                    item.timestamp,
                    item.model,
                    item.provider,
                    item.agent,
                    item.task_type,
                    item.tokens_in,
                    item.tokens_out,
                    item.cost_usd,
                    item.request_summary,
                    item.fallback_from,
                ),
            )
        return item

    def total_cost(
        self,
        since: datetime | None = None,
        model_prefix: str | None = None,
    ) -> float:
        """Total cost in USD, optionally filtered by date and model prefix."""
        query = "SELECT COALESCE(SUM(cost_usd), 0) FROM model_usage WHERE 1=1"
        params: list = []
        if since:
            query += " AND timestamp >= ?"
            params.append(since.isoformat())
        if model_prefix:
            query += " AND model LIKE ?"
            params.append(f"{model_prefix}%")
        with self._conn() as conn:
            row = conn.execute(query, params).fetchone()
        return float(row[0]) if row else 0.0

    def summary_by_model(
        self, since: datetime | None = None
    ) -> list[dict]:
        query = """
            SELECT model, COUNT(*) as calls,
                   SUM(tokens_in) as tokens_in,
                   SUM(tokens_out) as tokens_out,
                   SUM(cost_usd) as cost_usd
            FROM model_usage
            WHERE 1=1
        """
        params: list = []
        if since:
            query += " AND timestamp >= ?"
            params.append(since.isoformat())
        query += " GROUP BY model ORDER BY cost_usd DESC"
        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def summary_by_agent(
        self, since: datetime | None = None
    ) -> list[dict]:
        query = """
            SELECT agent, COUNT(*) as calls,
                   SUM(cost_usd) as cost_usd
            FROM model_usage
            WHERE 1=1
        """
        params: list = []
        if since:
            query += " AND timestamp >= ?"
            params.append(since.isoformat())
        query += " GROUP BY agent ORDER BY cost_usd DESC"
        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def recent(self, limit: int = 50) -> list[UsageRecord]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM model_usage
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [UsageRecord(**dict(r)) for r in rows]
