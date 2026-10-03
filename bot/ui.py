"""show_screen: the single place handlers go through to change what the
customer sees. Text-only. It edits the originating message in place when it
can, and otherwise sends a fresh message: when the message is inaccessible (a
MaybeInaccessibleMessage that is not a real Message) or older than 48 hours
(Telegram's limit for acting on old messages), neither edit nor delete is
attempted. It swallows the "message is not modified" no-op every rapid
double-tap produces, and falls back to delete+resend for any other BadRequest,
so the customer is never left staring at a dead screen.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from telegram import InlineKeyboardMarkup, Message, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

EDIT_WINDOW = timedelta(hours=48)


def _is_editable(message: object) -> bool:
    if not isinstance(message, Message):
        return False
    return datetime.now(UTC) - message.date < EDIT_WINDOW


async def show_screen(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    text: str,
    keyboard: InlineKeyboardMarkup,
    alert: str | None = None,
) -> None:
    query = update.callback_query
    if query is not None:
        if alert:
            await query.answer(alert, show_alert=True)
        else:
            await query.answer()

    old_message = query.message if query is not None else None
    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None and old_message is not None:
        chat_id = old_message.chat.id

    if not isinstance(old_message, Message) or not _is_editable(old_message):
        await _send_new(context, chat_id, text, keyboard)
        return

    try:
        await old_message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    except BadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return
        logger.warning("BadRequest in show_screen, falling back to resend: %s", exc)
        try:  # noqa: SIM105 - explicit try/except keeps the intent obvious
            await old_message.delete()
        except BadRequest:
            pass
        await _send_new(context, chat_id, text, keyboard)


async def _send_new(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int | None,
    text: str,
    keyboard: InlineKeyboardMarkup,
) -> None:
    if chat_id is None:
        logger.warning("show_screen: no chat to send to; dropping screen")
        return
    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )
