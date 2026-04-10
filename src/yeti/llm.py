"""LLM wrapper — cost tracking, monthly cap, automatic fallback."""

import logging
from datetime import UTC, datetime

import httpx
import litellm

from yeti.config import settings
from yeti.models.usage import UsageRecord, UsageStore

logger = logging.getLogger(__name__)

_store = UsageStore()

# Track which alert thresholds have already fired this month
_alerts_sent: set[int] = set()
_alerts_month: str = ""


def _month_start() -> datetime:
    """Return the first day of the current UTC month."""
    now = datetime.now(UTC)
    return now.replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )


def monthly_paid_spend() -> float:
    """Total spend on paid models this month (excludes local Ollama)."""
    since = _month_start()
    total = _store.total_cost(since=since, model_prefix="claude")
    total += _store.total_cost(
        since=since, model_prefix="anthropic"
    )
    total += _store.total_cost(
        since=since, model_prefix="gpt"
    )
    total += _store.total_cost(
        since=since, model_prefix="openai"
    )
    return total


def _provider_for(model: str) -> str:
    if "claude" in model or "anthropic" in model:
        return "anthropic"
    if "gpt" in model or "openai" in model:
        return "openai"
    if "ollama" in model or "llama" in model:
        return "ollama"
    return "unknown"


def _is_paid(model: str) -> bool:
    return _provider_for(model) in ("anthropic", "openai")


def _select_model(
    requested: str, monthly_spent: float
) -> tuple[str, str]:
    """Choose the actual model to use based on budget.

    Returns (model_to_use, fallback_from). fallback_from is empty
    if the requested model is being used.
    """
    cap = settings.monthly_budget_eur * settings.eur_to_usd
    if not _is_paid(requested) or monthly_spent < cap:
        return (requested, "")

    # Hard cap exceeded — fall back to local
    fallback = settings.litellm_local_model
    logger.warning(
        "Monthly budget cap exceeded (%.2f USD), "
        "falling back from %s to %s",
        monthly_spent,
        requested,
        fallback,
    )
    return (fallback, requested)


async def _send_telegram(message: str) -> None:
    """Best-effort Telegram alert."""
    if not settings.telegram_bot_token:
        return
    if not settings.telegram_allowed_chat_id:
        return
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(
                f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                json={
                    "chat_id": settings.telegram_allowed_chat_id,
                    "text": message,
                },
            )
    except Exception:
        logger.exception("Failed to send Telegram alert")


async def _maybe_alert(spent_usd: float) -> None:
    """Fire one-time alerts when crossing budget thresholds."""
    global _alerts_sent, _alerts_month

    now = datetime.now(UTC)
    current_month = now.strftime("%Y-%m")
    if current_month != _alerts_month:
        _alerts_month = current_month
        _alerts_sent = set()

    cap_usd = settings.monthly_budget_eur * settings.eur_to_usd
    if cap_usd <= 0:
        return
    pct = int(spent_usd / cap_usd * 100)

    for threshold in (settings.budget_alert_pct, 100):
        if pct >= threshold and threshold not in _alerts_sent:
            _alerts_sent.add(threshold)
            await _send_telegram(
                f"YETI budget alert: {pct}% used "
                f"({spent_usd:.2f} USD of {cap_usd:.2f} USD cap). "
                + (
                    "Falling back to local model."
                    if threshold >= 100
                    else "Approaching cap."
                )
            )


async def acompletion(
    *,
    model: str,
    messages: list,
    agent: str = "",
    task_type: str = "",
    request_summary: str = "",
    **kwargs,
):
    """Drop-in replacement for litellm.acompletion with cost tracking."""
    spent = monthly_paid_spend()
    actual_model, fallback_from = _select_model(model, spent)

    # If this falls back from anthropic/openai to ollama, swap api_key
    if fallback_from and "ollama" in actual_model:
        kwargs.pop("api_key", None)
        kwargs["api_base"] = settings.ollama_base_url

    response = await litellm.acompletion(
        model=actual_model,
        messages=messages,
        **kwargs,
    )

    # Capture usage
    try:
        usage = response.usage if hasattr(response, "usage") else None
        tokens_in = (
            getattr(usage, "prompt_tokens", 0) if usage else 0
        )
        tokens_out = (
            getattr(usage, "completion_tokens", 0)
            if usage
            else 0
        )
        cost = 0.0
        try:
            cost = litellm.completion_cost(
                completion_response=response
            ) or 0.0
        except Exception:
            cost = 0.0

        _store.record(
            UsageRecord(
                model=actual_model,
                provider=_provider_for(actual_model),
                agent=agent,
                task_type=task_type,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=cost,
                request_summary=request_summary[:200],
                fallback_from=fallback_from,
            )
        )

        if cost > 0:
            await _maybe_alert(spent + cost)
    except Exception:
        logger.exception("Failed to record usage")

    return response
