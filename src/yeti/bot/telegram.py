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
from yeti.models.tasks import Task, TaskStatus, TaskStore

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
        "/actions — pending tasks\n"
        "/inbox — pending inbox items\n"
        "/add <title> — create task\n"
        "/note <text> — capture note for triage\n"
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

    store = TaskStore()
    active = store.list(status=TaskStatus.ACTIVE)

    if not active:
        await update.message.reply_text("No active tasks.")
        return

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
    store = TaskStore()
    item = store.create(
        Task(title=title, source="telegram")
    )
    await update.message.reply_text(
        f"Created: {item.title} ({item.id[:8]})"
    )


def _inbox_keyboard(item) -> InlineKeyboardMarkup | None:
    """Build inline keyboard for an inbox item based on its quick_actions.

    Items with a non-empty answer_schema (multi-field forms) cannot be
    resolved by a single button — render only a dashboard deep link, or
    omit the keyboard entirely if no public URL is configured.
    """
    if item.answer_schema:
        if settings.dashboard_public_url:
            url = (
                settings.dashboard_public_url.rstrip("/")
                + "/dashboard/inbox"
            )
            return InlineKeyboardMarkup(
                [[InlineKeyboardButton("Open in dashboard", url=url)]]
            )
        return None

    action_buttons = {
        "discard": ("Discard", f"inbox_discard:{item.id}"),
        "convert_to_task": (
            "\u2192 Task",
            f"inbox_convert:{item.id}",
        ),
        "approve_as_task": ("Approve", f"inbox_approve:{item.id}"),
    }
    row = []
    for action in item.quick_actions:
        btn = action_buttons.get(action)
        if btn:
            row.append(
                InlineKeyboardButton(btn[0], callback_data=btn[1])
            )
    if not row:
        return None
    return InlineKeyboardMarkup([row])


async def cmd_inbox(update: Update, context) -> None:
    if not _is_authorized(update):
        return

    from yeti.models.inbox import InboxStore

    items = InboxStore().list_pending()

    if not items:
        await update.message.reply_text("Inbox is empty.")
        return

    for item in items:
        summary = (item.summary or "")[:300]
        body = f"[{item.type.value}] {item.title}"
        if summary:
            body += f"\n{summary}"
        keyboard = _inbox_keyboard(item)
        if keyboard is None:
            body += (
                "\n\n(This item needs a full form — "
                "resolve in the dashboard.)"
            )
        await update.message.reply_text(
            body, reply_markup=keyboard
        )


async def cmd_note(update: Update, context) -> None:
    """Capture a note for triage."""
    if not _is_authorized(update):
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: /note <text>\n"
            "Or send any long message and reply with /note"
        )
        return

    content = " ".join(context.args)
    from yeti.models.notes import Note, NoteSource, NoteStore

    store = NoteStore()
    note = store.create(
        Note(
            content=content,
            source=NoteSource.TELEGRAM,
        )
    )

    # Queue triage
    try:
        from yeti.worker import triage_note

        triage_note.delay(note.id)
        await update.message.reply_text(
            f"Note captured ({note.id[:8]}). "
            "Triaging in background..."
        )
    except Exception:
        await update.message.reply_text(
            f"Note saved ({note.id[:8]}) but worker unavailable."
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

    if action.startswith("inbox_"):
        await _handle_inbox_callback(query, action, item_id)
        return

    store = TaskStore()
    status_map = {
        "approve": TaskStatus.ACTIVE,
        "reject": TaskStatus.CANCELLED,
        "complete": TaskStatus.COMPLETED,
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


async def _handle_inbox_callback(
    query, action: str, item_id: str
) -> None:
    """Resolve an inbox item from a Telegram inline button press."""
    from yeti.models.inbox import InboxStore

    inbox = InboxStore()
    item = inbox.get(item_id)
    if not item:
        await query.edit_message_text("Inbox item not found.")
        return

    if action == "inbox_discard":
        inbox.resolve(item_id, "discarded")
        await query.edit_message_text(
            f"DISCARDED: {item.title}"
        )
        return

    if action in ("inbox_convert", "inbox_approve"):
        import httpx

        path = (
            "convert-to-task"
            if action == "inbox_convert"
            else "approve-task"
        )
        url = f"http://localhost:8000/api/inbox/{item_id}/{path}"
        body: dict = (
            {"answer": {"title": item.title}}
            if action == "inbox_approve"
            else {}
        )
        headers = (
            {"x-api-key": settings.dashboard_api_key}
            if settings.dashboard_api_key
            else {}
        )
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post(url, json=body, headers=headers)
        except Exception:
            logger.exception("Inbox callback HTTP failed")
            await query.edit_message_text(
                f"Could not reach API for: {item.title}"
            )
            return

        if r.status_code >= 400:
            await query.edit_message_text(
                f"API error {r.status_code}: {item.title}"
            )
            return

        label = (
            "CONVERTED"
            if action == "inbox_convert"
            else "APPROVED"
        )
        await query.edit_message_text(f"{label}: {item.title}")
        return

    await query.edit_message_text(
        f"Unknown inbox action: {action}"
    )


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

    photo = update.message.photo[-1]
    file = await photo.get_file()
    image_bytes = bytes(await file.download_as_bytearray())
    caption = update.message.caption or ""

    # Always save the image first — never lose data
    from yeti.vision.storage import save_image

    image_id = save_image(image_bytes)

    from yeti.vision.extract import extract

    result = await extract(image_bytes, caption)
    confidence = result.get("confidence", 0.0)
    needs_review = result.get("needs_review", True)
    data = result.get("structured")

    lines = [
        f"Method: {result.get('method', 'unknown')}",
        f"Confidence: {confidence:.0%}",
    ]

    if "error" in result:
        lines.append(f"Error: {result['error']}")

    if data:
        lines.append(json.dumps(data, indent=2))

    raw = result.get("raw_text", "")
    if raw:
        lines.append(f"\nRaw OCR:\n{raw[:500]}")

    # Decide where to send it
    if not needs_review and data:
        wing, room = _wing_for_doc_type(
            data.get("type", "")
        )
        try:
            from yeti.memory.client import MemPalaceClient

            mem = MemPalaceClient()
            content = json.dumps(data, indent=2)
            if caption:
                content = f"Context: {caption}\n\n{content}"
            content += f"\n\nImage: /api/images/{image_id}"
            await mem.store(
                content=content,
                wing=wing,
                room=room,
                source="telegram-photo",
            )
            lines.append(f"\nStored in memory ({wing}/{room})")
        except Exception:
            logger.exception("Failed to store in memory")
    else:
        # Low confidence — create inbox item for manual review
        try:
            from yeti.models.inbox import (
                InboxItem,
                InboxStore,
                InboxType,
            )

            inbox = InboxStore()
            inbox.create(
                InboxItem(
                    type=InboxType.NOTIFICATION,
                    title="Image needs manual review",
                    summary=(
                        f"OCR confidence {confidence:.0%}. "
                        f"Caption: {caption}"
                        if caption
                        else f"OCR confidence {confidence:.0%}."
                    ),
                    payload={
                        "image_id": image_id,
                        "method": result.get("method", ""),
                        "confidence": confidence,
                        "raw_text": raw,
                        "extracted": data or {},
                        "caption": caption,
                    },
                    source="telegram-photo",
                    confidence=confidence,
                )
            )
            lines.append(
                "\nLow confidence — saved to inbox for review"
            )
        except Exception:
            logger.exception("Failed to create inbox item")

    response = "\n".join(lines)
    if len(response) > 4000:
        response = response[:4000] + "\n...(truncated)"

    await update.message.reply_text(response)


def _wing_for_doc_type(doc_type: str) -> tuple[str, str]:
    if doc_type == "business_card":
        return ("people", "contacts")
    if doc_type == "receipt":
        return ("finance", "receipts")
    return ("general", "documents")


def _build_app() -> Application:
    """Build the Telegram application with all handlers."""
    app = Application.builder().token(
        settings.telegram_bot_token
    ).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("actions", cmd_actions))
    app.add_handler(CommandHandler("inbox", cmd_inbox))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("note", cmd_note))
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
