"""owner_only decorator for command and callback handlers.

Register admin handlers with filters.User(get_settings().owner_id) as well — this
decorator is a second line of defense, not the only one.
"""

from __future__ import annotations

import functools
import logging
import sqlite3
from collections.abc import Callable, Coroutine
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from .config import get_settings
from .repo import orders as orders_repo

logger = logging.getLogger(__name__)

HandlerFunc = Callable[[Update, ContextTypes.DEFAULT_TYPE], Coroutine[Any, Any, None]]

_ORDER_UNAVAILABLE_MESSAGE = "That order is no longer available."


def owner_only(func: HandlerFunc) -> HandlerFunc:
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        user_id = user.id if user else None

        if user_id != get_settings().owner_id:
            logger.warning("Refused owner-only action to user_id=%s", user_id)
            if update.callback_query:
                await update.callback_query.answer(
                    "You are not authorized to do that.", show_alert=True
                )
            elif update.message:
                await update.message.reply_text("You are not authorized to do that.")
            return

        await func(update, context)

    return wrapper


async def get_owned_order(update: Update, order_id: int) -> sqlite3.Row | None:
    """Fetch an order and verify the caller owns it. Use this everywhere a
    customer-facing handler takes an order_id from callback data — without
    it, one customer can act on another customer's order by guessing or
    crafting an id (Telegram does not bind callback_data to the message it
    was originally attached to).

    On any mismatch — including a nonexistent order — logs at WARNING and
    answers the callback query (or replies to the message) with a generic
    alert. The alert is deliberately identical whether the order doesn't
    exist or simply isn't the caller's, so the response can't be used to
    probe which order ids exist. Returns the order row on success, else
    None (the caller should just return once it sees None — the response
    has already been sent).
    """
    user = update.effective_user
    user_id = user.id if user else None
    order = orders_repo.get(order_id)

    if order is None or order["user_id"] != user_id:
        logger.warning(
            "Order ownership check failed: user_id=%s requested order_id=%s", user_id, order_id
        )
        if update.callback_query:
            await update.callback_query.answer(_ORDER_UNAVAILABLE_MESSAGE, show_alert=True)
        elif update.message:
            await update.message.reply_text(_ORDER_UNAVAILABLE_MESSAGE)
        return None

    return order
