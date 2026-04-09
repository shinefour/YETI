"""YETI Telegram Bot — mobile interface."""

import json
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from yeti.agents.chat import chat
from yeti.config import settings
from yeti.models.actions import ActionItem, ActionStatus, ActionStore

logger = logging.getLogger(__name__)


def _is_authorized(update: Update) -> bool:
    """Check if the message is from Daniel's authorized chat ID."""
    if settings.telegram_allowed_chat_id == 0:
        logger.warning(
            "YETI_TELEGRAM_ALLOWED_CHAT_ID not set "
            "— accepting all messages"
        )
        return True
    chat_id = (
        update.effective_chat.id if update.effective_chat else 0
    )
    return chat_id == settings.telegram_allowed_chat_id


async def cmd_start(update: Update, context) -> None:
    if not _is_authorized(update):
        return
    await update.message.reply_text(
        "YETI is online. Commands:\n"
        "/status — system status\n"
        "/actions — pending action items\n"
        "/add <title> — create action item\n"
        "Or just send a message to chat."
    )


async def cmd_status(update: Update, context) -> None:
    if not _is_authorized(update):
        return

    import httpx

    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get("http://localhost:8000/api/status")
            data = r.json()

        lines = ["YETI Status\n"]
        for name, state in data.get("services", {}).items():
            icon = "+" if state == "up" else "-"
            lines.append(f"  {icon} {name}: {state}")

        lines.append("")
        for name, state in data.get("integrations", {}).items():
            icon = "+" if state == "connected" else "."
            lines.append(f"  {icon} {name}: {state}")

        await update.message.reply_text("\n".join(lines))
    except Exception:
        await update.message.reply_text("Could not reach API.")


async def cmd_actions(update: Update, context) -> None:
    if not _is_authorized(update):
        return

    store = ActionStore()
    pending = store.list(status=ActionStatus.PENDING_REVIEW)
    active = store.list(status=ActionStatus.ACTIVE)

    if not pending and not active:
        await update.message.reply_text("No action items.")
        return

    for item in pending:
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "Approve",
                        callback_data=f"approve:{item.id}",
                    ),
                    InlineKeyboardButton(
                        "Reject",
                        callback_data=f"reject:{item.id}",
                    ),
                ]
            ]
        )
        project = f" [{item.project}]" if item.project else ""
        await update.message.reply_text(
            f"PENDING: {item.title}{project}",
            reply_markup=keyboard,
        )

    for item in active:
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "Done",
                        callback_data=f"complete:{item.id}",
                    ),
                ]
            ]
        )
        project = f" [{item.project}]" if item.project else ""
        await update.message.reply_text(
            f"ACTIVE: {item.title}{project}",
            reply_markup=keyboard,
        )


async def cmd_add(update: Update, context) -> None:
    if not _is_authorized(update):
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: /add <title>"
        )
        return

    title = " ".join(context.args)
    store = ActionStore()
    item = store.create(
        ActionItem(title=title, source="telegram")
    )
    await update.message.reply_text(
        f"Created: {item.title} ({item.id[:8]})"
    )


async def handle_callback(
    update: Update, context
) -> None:
    """Handle inline keyboard button presses."""
    query = update.callback_query
    if not _is_authorized(update):
        await query.answer("Unauthorized")
        return

    await query.answer()
    data = query.data
    action, item_id = data.split(":", 1)

    store = ActionStore()
    status_map = {
        "approve": ActionStatus.ACTIVE,
        "reject": ActionStatus.CANCELLED,
        "complete": ActionStatus.COMPLETED,
    }

    new_status = status_map.get(action)
    if not new_status:
        return

    item = store.update_status(item_id, new_status)
    if item:
        await query.edit_message_text(
            f"{new_status.value.upper()}: {item.title}"
        )
    else:
        await query.edit_message_text("Item not found.")


async def handle_message(
    update: Update, context
) -> None:
    if not _is_authorized(update):
        return

    user_message = update.message.text
    logger.info(
        "Message from %s: %s",
        update.effective_chat.id,
        user_message[:50],
    )

    try:
        response = await chat(user_message)
        await update.message.reply_text(response)
    except Exception:
        logger.exception("Chat agent error")
        await update.message.reply_text(
            "Something went wrong. Check logs."
        )


async def handle_photo(
    update: Update, context
) -> None:
    if not _is_authorized(update):
        return

    await update.message.reply_text("Analyzing image...")

    photo = update.message.photo[-1]  # highest resolution
    file = await photo.get_file()
    image_bytes = await file.download_as_bytearray()

    from yeti.vision.extract import extract_both

    results = await extract_both(bytes(image_bytes))

    lines = []
    for method, result in results.items():
        label = method.replace("_", " + ").upper()
        lines.append(f"--- {label} ---")
        if "error" in result:
            lines.append(f"Error: {result['error']}")
        else:
            data = result.get("structured")
            if data:
                lines.append(json.dumps(data, indent=2))
            else:
                lines.append("Could not parse structured data")
            raw = result.get("raw_text", "")
            if raw:
                lines.append(f"\nRaw OCR:\n{raw[:500]}")
        lines.append("")

    response = "\n".join(lines)
    # Telegram has a 4096 char limit
    if len(response) > 4000:
        response = response[:4000] + "\n...(truncated)"

    await update.message.reply_text(response)


def _build_app() -> Application:
    """Build the Telegram application with all handlers."""
    app = Application.builder().token(
        settings.telegram_bot_token
    ).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("actions", cmd_actions))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CallbackQueryHandler(handle_callback))
    text_filter = filters.TEXT & ~filters.COMMAND
    app.add_handler(MessageHandler(text_filter, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    return app


def main():
    """Start the Telegram bot with retry on conflict."""
    import time

    if not settings.telegram_bot_token:
        logger.error(
            "YETI_TELEGRAM_BOT_TOKEN not set — "
            "cannot start Telegram bot"
        )
        return

    max_retries = 5
    for attempt in range(1, max_retries + 1):
        logger.info(
            "YETI Telegram bot starting (attempt %d)...",
            attempt,
        )
        try:
            app = _build_app()
            app.run_polling(drop_pending_updates=True)
            break  # clean shutdown
        except Exception as e:
            if "Conflict" in str(e) and attempt < max_retries:
                wait = attempt * 10
                logger.warning(
                    "Telegram conflict — another session "
                    "still active. Waiting %ds...",
                    wait,
                )
                time.sleep(wait)
            else:
                logger.exception("Telegram bot failed")
                raise


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
