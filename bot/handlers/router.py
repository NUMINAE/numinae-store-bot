"""The single free-text MessageHandler. Routing order: reply-keyboard menu
buttons (exact label match) first; then, for an owner with a pending admin
input, the admin branch; everything else gets a gentle nudge back to the menu.
The owner can still browse and buy as a customer whenever nothing is pending.
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from ..config import get_settings
from ..i18n import t
from ..keyboards import MENU_ACTION_BY_LABEL, main_reply_keyboard
from ..repo import admin_pending as admin_pending_repo
from ..tg import require_message, require_text, require_user
from . import admin_catalogue, catalogue, library
from .common import get_lang, render_main_menu, touch_customer

logger = logging.getLogger(__name__)


async def _restore_reply_keyboard(
    update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str
) -> None:
    chat = update.effective_chat
    await context.bot.send_message(
        chat_id=chat.id if chat is not None else require_user(update).id,
        text=t("common.menu_restored", lang),
        reply_markup=main_reply_keyboard(lang),
    )


async def _dispatch_menu_button(
    update: Update, context: ContextTypes.DEFAULT_TYPE, label: str
) -> bool:
    """Persistent-reply-keyboard taps arrive here as plain text messages, so
    they're matched by exact string only — never substring/startswith/
    case-fold — before any other branch gets a chance at the message.
    MENU_ACTION_BY_LABEL covers every label across both languages (see
    keyboards.py), so a customer who just switched language and still has
    the other language's keyboard cached doesn't get a dead button. Returns
    True if the label matched and the update was handled.

    cart is imported locally to keep this module's import graph shallow.
    """
    from . import cart as cart_handlers

    action = MENU_ACTION_BY_LABEL.get(label)
    if action == "browse":
        await catalogue.show_categories(update, context)
    elif action == "cart":
        await cart_handlers.show_cart(update, context)
    elif action == "library":
        await library.show_library(update, context, 0)
    else:
        return False
    return True


async def route_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = require_user(update)
    raw = require_text(require_message(update)).strip()
    touch_customer(update)

    if await _dispatch_menu_button(update, context, raw):
        return

    if user.id == get_settings().owner_id:
        pending = admin_pending_repo.get(user.id)
        if pending is not None:
            await admin_catalogue.handle_pending_input(update, context, pending)
            if admin_pending_repo.get(user.id) is None:
                # The input was applied, or the action was unrecognized and
                # fell back to the dashboard — either way nothing more is
                # expected to be typed. If validation failed instead, the
                # pending row is still there and the keyboard stays hidden.
                await _restore_reply_keyboard(update, context, get_lang(user.id))
            return
        # No pending admin input: fall through to the nudge so the owner can
        # still test the shop with their own account.

    await _nudge(update, context)


async def _nudge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = require_user(update).id
    lang = get_lang(user_id)
    menu_text, keyboard = render_main_menu(user_id, lang)
    await require_message(update).reply_text(
        t("common.nudge", lang, menu=menu_text),
        reply_markup=keyboard,
    )
