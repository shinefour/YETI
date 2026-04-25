"""Tests for memory retrieval logging (yeti.memory.usage)."""

from datetime import UTC, datetime, timedelta

import pytest

from yeti.memory.usage import UsageStore


@pytest.fixture
def store(tmp_path):
    db = tmp_path / "retrieval.db"
    return UsageStore(db_path=db)


def test_log_search_inserts_row(store):
    store.log_search("daniel mundt", source="chat")
    with store._conn() as conn:
        rows = conn.execute(
            "SELECT query, source, drawer_id, fact_subject "
            "FROM retrieval_log"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["query"] == "daniel mundt"
    assert rows[0]["source"] == "chat"
    assert rows[0]["drawer_id"] is None
    assert rows[0]["fact_subject"] is None


def test_log_search_skips_empty_query(store):
    store.log_search("", source="chat")
    with store._conn() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM retrieval_log"
        ).fetchone()[0]
    assert n == 0


def test_log_drawer_hits_one_row_per_id(store):
    store.log_drawer_hits(
        ["d1", "d2", "d3"], source="triage", query="x"
    )
    with store._conn() as conn:
        rows = conn.execute(
            "SELECT drawer_id, query, source FROM retrieval_log"
        ).fetchall()
    ids = {r["drawer_id"] for r in rows}
    assert ids == {"d1", "d2", "d3"}
    assert all(r["query"] == "x" for r in rows)
    assert all(r["source"] == "triage" for r in rows)


def test_log_drawer_hits_skips_empties(store):
    store.log_drawer_hits(["", None, "d1"], source="api")  # type: ignore
    with store._conn() as conn:
        rows = conn.execute(
            "SELECT drawer_id FROM retrieval_log"
        ).fetchall()
    assert [r["drawer_id"] for r in rows] == ["d1"]


def test_log_kg_query(store):
    store.log_kg_query("Daniel Costa", source="chat")
    with store._conn() as conn:
        row = conn.execute(
            "SELECT fact_subject, source FROM retrieval_log"
        ).fetchone()
    assert row["fact_subject"] == "Daniel Costa"
    assert row["source"] == "chat"


def test_drawer_hit_count(store):
    store.log_drawer_hits(["d1", "d1", "d2"], source="chat")
    assert store.drawer_hit_count("d1") == 2
    assert store.drawer_hit_count("d2") == 1
    assert store.drawer_hit_count("never-seen") == 0


def test_drawer_hit_count_since(store):
    store.log_drawer_hits(["d1"], source="chat")
    future = datetime.now(UTC) + timedelta(hours=1)
    past = datetime.now(UTC) - timedelta(hours=1)
    assert store.drawer_hit_count("d1", since=past) == 1
    assert store.drawer_hit_count("d1", since=future) == 0


def test_entity_hit_count(store):
    store.log_kg_query("Sonia Scibor", source="triage")
    store.log_kg_query("Sonia Scibor", source="chat")
    store.log_kg_query("Daniel Costa", source="chat")
    assert store.entity_hit_count("Sonia Scibor") == 2
    assert store.entity_hit_count("Daniel Costa") == 1
    assert store.entity_hit_count("nobody") == 0
