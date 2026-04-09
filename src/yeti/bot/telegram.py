"""YETI Telegram Bot — mobile interface."""

import logging

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from yeti.agents.chat import chat
from yeti.config import settings

logger = logging.getLogger(__name__)


def _is_authorized(update: Update) -> bool:
    """Check if the message is from Daniel's authorized chat ID."""
    if settings.telegram_allowed_chat_id == 0:
        logger.warning("YETI_TELEGRAM_ALLOWED_CHAT_ID not set — accepting all messages")
        return True
    return update.effective_chat.id == settings.telegram_allowed_chat_id


async def cmd_start(update: Update, context) -> None:
    """Handle /start command."""
    if not _is_authorized(update):
        return
    await update.message.reply_text(
        "YETI is online. Send me a message to get started."
    )


async def cmd_status(update: Update, context) -> None:
    """Handle /status command."""
    if not _is_authorized(update):
        return
    await update.message.reply_text(
        "YETI v0.1.0 — System status: healthy\n"
        "Integrations: not yet configured\n"
        "Memory: not yet connected"
    )


async def handle_message(update: Update, context) -> None:
    """Handle incoming text messages — route to Chat Agent."""
    if not _is_authorized(update):
        return

    user_message = update.message.text
    logger.info("Message from %s: %s", update.effective_chat.id, user_message[:50])

    try:
        response = await chat(user_message)
        await update.message.reply_text(response)
    except Exception:
        logger.exception("Chat agent error")
        await update.message.reply_text(
            "Something went wrong processing your message. Check the logs."
        )


async def handle_photo(update: Update, context) -> None:
    """Handle incoming photos — route to vision model."""
    if not _is_authorized(update):
        return

    # TODO: Forward to vision model for analysis
    await update.message.reply_text(
        "Image analysis is not yet implemented. Coming soon!"
    )


def main():
    """Start the Telegram bot."""
    if not settings.telegram_bot_token:
        logger.error("YETI_TELEGRAM_BOT_TOKEN not set — cannot start Telegram bot")
        return

    application = Application.builder().token(settings.telegram_bot_token).build()

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("status", cmd_status))
    text_filter = filters.TEXT & ~filters.COMMAND
    application.add_handler(MessageHandler(text_filter, handle_message))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    logger.info("YETI Telegram bot starting...")
    application.run_polling()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
