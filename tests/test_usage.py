"""Tests for model usage tracking."""

from datetime import UTC, datetime, timedelta

import pytest

from yeti.models.usage import UsageRecord, UsageStore


@pytest.fixture
def store(tmp_path):
    return UsageStore(db_path=tmp_path / "usage.db")


def test_record_and_total(store):
    store.record(
        UsageRecord(
            model="claude-sonnet-4",
            cost_usd=0.05,
            tokens_in=100,
            tokens_out=50,
        )
    )
    store.record(
        UsageRecord(
            model="claude-haiku",
            cost_usd=0.001,
        )
    )
    store.record(
        UsageRecord(
            model="ollama/llama3",
            cost_usd=0.0,
        )
    )

    assert store.total_cost() == pytest.approx(0.051)
    assert store.total_cost(model_prefix="claude") == pytest.approx(0.051)
    assert store.total_cost(model_prefix="ollama") == 0.0


def test_total_cost_with_date_filter(store):
    old = datetime.now(UTC) - timedelta(days=40)
    store.record(
        UsageRecord(
            model="claude-sonnet-4",
            cost_usd=10.0,
            timestamp=old.isoformat(),
        )
    )
    store.record(
        UsageRecord(
            model="claude-sonnet-4",
            cost_usd=2.0,
        )
    )

    month_start = datetime.now(UTC).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    assert store.total_cost(since=month_start) == pytest.approx(2.0)


def test_summary_by_model(store):
    store.record(UsageRecord(model="claude-sonnet-4", cost_usd=0.10))
    store.record(UsageRecord(model="claude-sonnet-4", cost_usd=0.20))
    store.record(UsageRecord(model="claude-haiku", cost_usd=0.01))

    summary = store.summary_by_model()
    assert len(summary) == 2
    assert summary[0]["model"] == "claude-sonnet-4"
    assert summary[0]["calls"] == 2
    assert summary[0]["cost_usd"] == pytest.approx(0.30)


def test_summary_by_agent(store):
    store.record(
        UsageRecord(model="claude", cost_usd=0.5, agent="chat")
    )
    store.record(
        UsageRecord(model="claude", cost_usd=0.3, agent="chat")
    )
    store.record(
        UsageRecord(
            model="claude", cost_usd=0.1, agent="triage"
        )
    )

    summary = store.summary_by_agent()
    assert summary[0]["agent"] == "chat"
    assert summary[0]["cost_usd"] == pytest.approx(0.8)


def test_recent(store):
    for i in range(5):
        store.record(
            UsageRecord(
                model=f"model-{i}",
                cost_usd=0.01,
            )
        )

    recent = store.recent(limit=3)
    assert len(recent) == 3


def test_select_model_under_budget():
    from yeti import llm

    actual, fallback = llm._select_model(
        "claude-sonnet-4-20250514", monthly_spent=1.0
    )
    assert actual == "claude-sonnet-4-20250514"
    assert fallback == ""


def test_select_model_over_budget(monkeypatch):
    from yeti import llm
    from yeti.config import settings

    monkeypatch.setattr(settings, "monthly_budget_eur", 1.0)
    monkeypatch.setattr(settings, "eur_to_usd", 1.0)
    monkeypatch.setattr(
        settings, "litellm_local_model", "ollama/llama3"
    )

    actual, fallback = llm._select_model(
        "claude-sonnet-4-20250514", monthly_spent=10.0
    )
    assert actual == "ollama/llama3"
    assert fallback == "claude-sonnet-4-20250514"


def test_local_model_never_falls_back():
    from yeti import llm

    actual, fallback = llm._select_model(
        "ollama/llama3", monthly_spent=1000.0
    )
    assert actual == "ollama/llama3"
    assert fallback == ""
