"""Tests for the daily-summary metrics collector + renderer."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from yeti.sleep import summary


@pytest.fixture
def fake_db(tmp_path, monkeypatch):
    db = tmp_path / "yeti.db"

    # Minimal schema mirroring the relevant columns.
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            """
            CREATE TABLE notes (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                triage_level TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE inbox (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE inbox_audit (
                id TEXT PRIMARY KEY,
                action TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                details TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE superseded_drawers (
                drawer_id TEXT PRIMARY KEY,
                ts TEXT NOT NULL
            )
            """
        )

    monkeypatch.setattr(summary, "DB_PATH", db)
    return db


def _ins(db, table, **cols):
    keys = ",".join(cols.keys())
    qs = ",".join("?" for _ in cols)
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            f"INSERT INTO {table} ({keys}) VALUES ({qs})",
            tuple(cols.values()),
        )


def test_collect_counts_triage_levels(fake_db):
    today = datetime.now(UTC).isoformat()
    _ins(
        fake_db,
        "notes",
        id="a",
        created_at=today,
        triage_level="full",
    )
    _ins(
        fake_db,
        "notes",
        id="b",
        created_at=today,
        triage_level="discard",
    )
    _ins(
        fake_db,
        "notes",
        id="c",
        created_at=today,
        triage_level="discard",
    )

    metrics = summary.collect_metrics(
        datetime.now(UTC) - timedelta(days=1)
    )
    assert metrics["notes_by_level"] == {
        "full": 1,
        "discard": 2,
    }


def test_collect_counts_inbox_activity(fake_db):
    today = datetime.now(UTC).isoformat()
    _ins(fake_db, "inbox", id="i1", created_at=today)
    _ins(fake_db, "inbox", id="i2", created_at=today)
    _ins(
        fake_db,
        "inbox_audit",
        id="a1",
        action="resolved",
        timestamp=today,
        details=json.dumps({"resolution": "discarded"}),
    )
    _ins(
        fake_db,
        "inbox_audit",
        id="a2",
        action="resolved",
        timestamp=today,
        details=json.dumps(
            {
                "resolution": "discarded",
                "note": "auto-applied per resolution pattern",
            }
        ),
    )

    metrics = summary.collect_metrics(
        datetime.now(UTC) - timedelta(days=1)
    )
    assert metrics["inbox_created"] == 2
    assert metrics["inbox_resolved"] == 2
    assert metrics["inbox_auto_resolved"] == 1


def test_collect_counts_superseded(fake_db):
    today = datetime.now(UTC).isoformat()
    _ins(fake_db, "superseded_drawers", drawer_id="d1", ts=today)
    _ins(fake_db, "superseded_drawers", drawer_id="d2", ts=today)

    metrics = summary.collect_metrics(
        datetime.now(UTC) - timedelta(days=1)
    )
    assert metrics["drawers_superseded"] == 2


def test_collect_excludes_old_rows(fake_db):
    old = (
        datetime.now(UTC) - timedelta(days=7)
    ).isoformat()
    _ins(
        fake_db,
        "notes",
        id="old",
        created_at=old,
        triage_level="full",
    )
    metrics = summary.collect_metrics(
        datetime.now(UTC) - timedelta(days=1)
    )
    assert metrics["notes_by_level"] == {}


def test_render_minimal_summary():
    metrics = {
        "since": "2026-04-24T00:00:00+00:00",
        "until": "2026-04-25T23:30:00+00:00",
        "notes_by_level": {"full": 3, "discard": 7},
        "inbox_created": 4,
        "inbox_resolved": 2,
        "inbox_auto_resolved": 1,
        "drawers_superseded": 5,
    }
    body = summary.render_summary("2026-04-25", metrics)
    assert "Daily Summary — 2026-04-25" in body
    assert "full: 3" in body
    assert "discard: 7" in body
    assert "created: 4" in body
    assert "resolved: 2" in body
    assert "auto-resolved: 1" in body
    assert "drawers superseded: 5" in body


def test_render_skips_zero_sections():
    metrics = {
        "since": "x",
        "until": "y",
        "notes_by_level": {},
        "inbox_created": 0,
        "inbox_resolved": 0,
        "inbox_auto_resolved": 0,
        "drawers_superseded": 0,
    }
    body = summary.render_summary("2026-04-25", metrics)
    assert "Memory hygiene" not in body
    assert "Triage volume" not in body
    assert "Inbox activity" in body  # always shown
