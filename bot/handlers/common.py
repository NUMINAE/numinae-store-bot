"""/start, the Home screen, the "Help" screen, the shared "menu:show" callback,
and the catch-all unknown-callback fallback."""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from .. import owner_notify, profile
from ..i18n import t
from ..keyboards import cb, main_reply_keyboard
from ..render import esc
from ..repo import admin_pending as admin_pending_repo
from ..repo import cart, customers
from ..repo import settings as settings_repo
from ..tg import require_message, require_query, require_user
from ..ui import show_screen
from ..ui_kit import ICON, nav_row, screen, trim

logger = logging.getLogger(__name__)

WELCOME_BUDGET = 700  # raw characters; even all "&" (escaped x5) stays well under 4096


def touch_customer(update: Update) -> bool:
    """Upsert the customer row. Called on /start and on every callback
    interaction so username/first_name/last_seen_at stay current. Returns
    whether this was a newly created row — only start() below acts on that,
    so a new customer is only ever reported to the owner via /start, even
    though every other handler calls this too.

    Failure-isolated: bookkeeping must never take a screen down, so a database
    error is logged and treated as "not a new customer"."""
    user = update.effective_user
    if user is None:
        return False
    try:
        return customers.upsert(user.id, user.username, user.first_name)
    except Exception:
        logger.error("Could not record customer activity (user_id=%s)", user.id, exc_info=True)
        return False


def render_main_menu(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """The Home screen: header, the owner's welcome text, the main menu."""
    item_count, _ = cart.summary(user_id)
    cart_label = (
        t("common.my_cart_count", count=item_count) if item_count else t("common.my_cart_button")
    )
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(t("common.browse_button"), callback_data=cb("cat", "cats"))],
            [InlineKeyboardButton(cart_label, callback_data=cb("cart", "view"))],
            [
                InlineKeyboardButton(
                    t("common.my_library_button"), callback_data=cb("lib", "list", 0)
                )
            ],
            [InlineKeyboardButton(t("common.help_button"), callback_data=cb("help", "show"))],
        ]
    )
    # welcome_text is a single string the owner types: trimmed raw, escaped
    # once. Only the built-in default comes from the string table.
    welcome = settings_repo.get("welcome_text")
    body = esc(trim(welcome, WELCOME_BUDGET)) if welcome else esc(t("common.welcome_default"))
    return screen(ICON.home, t("common.home_title"), body), keyboard


def _chat_id(update: Update) -> int:
    """The chat to send a standalone message to: the effective chat, else the
    user's private chat (same id)."""
    chat = update.effective_chat
    return chat.id if chat is not None else require_user(update).id


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = require_message(update)
    user = require_user(update)
    is_new_customer = touch_customer(update)

    text, keyboard = render_main_menu(user.id)
    await message.reply_text(text, reply_markup=keyboard)
    # A message can only carry one reply_markup — the inline main menu above
    # used that slot, so the persistent reply keyboard needs its own message.
    await message.reply_text(
        t("common.reply_keyboard_intro"),
        reply_markup=main_reply_keyboard(),
    )
    # The owner's command menu can only be set once they have opened the chat.
    await profile.maybe_retry_owner_scope(context.bot, user.id)
    if is_new_customer:
        # After the customer's own replies, never before — a slow or failing
        # owner notification must not delay what they see.
        await owner_notify.notify_new_customer(context, user)


async def menu_show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    touch_customer(update)
    user_id = require_user(update).id
    # Going Home abandons an admin typed-input prompt: otherwise the next thing
    # the owner types would still be swallowed as that prompt's answer.
    admin_pending_repo.clear(user_id)
    text, keyboard = render_main_menu(user_id)
    await show_screen(update, context, text=text, keyboard=keyboard)


async def help_placeholder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    touch_customer(update)
    text = screen(ICON.info, t("common.help_title"), esc(t("common.help_text")))
    await show_screen(update, context, text=text, keyboard=InlineKeyboardMarkup([nav_row()]))


async def unknown_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = require_query(update)
    if query.data == "noop":
        await query.answer()
        return
    user = update.effective_user
    user_id = user.id if user else None
    logger.info("Unrecognized callback_data=%r from user_id=%s", query.data, user_id)
    await query.answer(t("common.expired_menu"), show_alert=True)
    if query.message:
        text, keyboard = (
            render_main_menu(user_id)
            if user_id
            else (
                screen(ICON.home, t("common.home_title"), esc(t("common.welcome_default"))),
                InlineKeyboardMarkup([]),
            )
        )
        await context.bot.send_message(
            chat_id=query.message.chat.id, text=text, reply_markup=keyboard
        )
