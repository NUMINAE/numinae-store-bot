"""show_screen: the single place catalogue/cart handlers go through to change
what the customer sees. Text-only — product photos were removed in Phase 2.5.
It swallows the "message is not modified" no-op every rapid double-tap
produces, and falls back to delete+resend for any other BadRequest (including
an edit attempted against a leftover photo message from before this phase)
so the customer is never left staring at a dead screen.
"""

from __future__ import annotations

import logging

from telegram import InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


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

    if old_message is None:
        await _send_new(context, chat_id, text, keyboard)
        return

    try:
        await old_message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)  # type: ignore[attr-defined]  # message may be inaccessible; see report
    except BadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return
        logger.warning("BadRequest in show_screen, falling back to resend: %s", exc)
        try:  # noqa: SIM105 - kept as explicit try/except; contextlib.suppress would be a code change
            await old_message.delete()  # type: ignore[attr-defined]  # message may be inaccessible; see report
        except BadRequest:
            pass
        await _send_new(context, chat_id, text, keyboard)


async def _send_new(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int | None,
    text: str,
    keyboard: InlineKeyboardMarkup,
) -> None:
    await context.bot.send_message(
        chat_id=chat_id,  # type: ignore[arg-type]  # chat_id may be None; see report
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )
