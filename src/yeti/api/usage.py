"""Model usage API — query cost and token data."""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter

from yeti.config import settings
from yeti.llm import _month_start, monthly_paid_spend
from yeti.models.usage import UsageStore

router = APIRouter(prefix="/api/usage", tags=["usage"])

_store = UsageStore()


@router.get("/summary")
async def usage_summary():
    """Current month spend, budget status, breakdowns."""
    spent_usd = monthly_paid_spend()
    cap_usd = settings.monthly_budget_eur * settings.eur_to_usd
    pct = (spent_usd / cap_usd * 100) if cap_usd > 0 else 0

    today_start = datetime.now(UTC).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    week_start = today_start - timedelta(days=7)
    month_start = _month_start()

    return {
        "today_usd": _store.total_cost(since=today_start),
        "week_usd": _store.total_cost(since=week_start),
        "month_usd": _store.total_cost(since=month_start),
        "month_paid_usd": spent_usd,
        "budget_eur": settings.monthly_budget_eur,
        "budget_usd": cap_usd,
        "budget_used_pct": round(pct, 1),
        "alert_threshold_pct": settings.budget_alert_pct,
        "by_model": _store.summary_by_model(since=month_start),
        "by_agent": _store.summary_by_agent(since=month_start),
    }


@router.get("/recent")
async def recent_usage(limit: int = 50):
    """Recent LLM calls."""
    items = _store.recent(limit=limit)
    return [item.model_dump() for item in items]
