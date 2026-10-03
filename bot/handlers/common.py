"""/start, the main menu, the temporary "My Library" screen and the "Help"
screen, the shared "menu:show" callback, and the catch-all unknown-callback
fallback."""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from .. import owner_notify
from ..i18n import DEFAULT_LANGUAGE, LANGUAGES, t
from ..keyboards import cb, main_reply_keyboard
from ..render import esc
from ..repo import cart, customers
from ..repo import settings as settings_repo
from ..tg import require_data, require_message, require_query, require_user
from ..ui import show_screen

logger = logging.getLogger(__name__)


def touch_customer(update: Update) -> bool:
    """Upsert the customer row. Called on /start and on every callback
    interaction so username/first_name/last_seen_at stay current. Returns
    whether this was a newly created row — only start() below acts on that,
    so a new customer is only ever reported to the owner via /start, even
    though every other handler calls this too."""
    user = update.effective_user
    if user is None:
        return False
    return customers.upsert(user.id, user.username, user.first_name)


def get_lang(user_id: int) -> str:
    """Resolve the customer's stored language once; callers pass it down
    rather than re-querying it per sub-screen within the same render."""
    customer = customers.get(user_id)
    lang = customer["language"] if customer else None
    return lang if lang in LANGUAGES else DEFAULT_LANGUAGE


def render_main_menu(user_id: int, lang: str) -> tuple[str, InlineKeyboardMarkup]:
    item_count, _ = cart.summary(user_id)
    cart_label = (
        t("common.my_cart_count", lang, count=item_count)
        if item_count
        else t("common.my_cart_button", lang)
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t("common.browse_button", lang), callback_data=cb("cat", "cats")
                )
            ],
            [InlineKeyboardButton(cart_label, callback_data=cb("cart", "view"))],
            [
                InlineKeyboardButton(
                    t("common.my_library_button", lang), callback_data=cb("lib", "show")
                )
            ],
            [InlineKeyboardButton(t("common.help_button", lang), callback_data=cb("help", "show"))],
            [
                InlineKeyboardButton(
                    t("common.language_button", lang), callback_data=cb("lang", "show")
                )
            ],
        ]
    )
    # welcome_text is a single string the owner types — not per-language.
    # Only the built-in default falls back to a translated string; a
    # configured value renders as typed.
    welcome = settings_repo.get("welcome_text")
    text = esc(welcome) if welcome else t("common.welcome_default", lang)
    return text, keyboard


async def show_language_picker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """The one screen shown before a new customer has chosen a language, so
    it can't assume either — both options are shown in their own language,
    with no surrounding prose. Also reachable any time via the main menu's
    Language / Язык button."""
    text = t("lang.picker", DEFAULT_LANGUAGE)
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t("lang.button_en", "en"), callback_data=cb("lang", "set", "en")
                ),
                InlineKeyboardButton(
                    t("lang.button_ru", "ru"), callback_data=cb("lang", "set", "ru")
                ),
            ]
        ]
    )
    await show_screen(update, context, text=text, keyboard=keyboard)


async def set_language(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str) -> None:
    user_id = require_user(update).id
    customers.set_language(user_id, lang)
    text, keyboard = render_main_menu(user_id, lang)
    await show_screen(update, context, text=text, keyboard=keyboard)
    # Re-send the reply keyboard immediately so it updates too.
    await context.bot.send_message(
        chat_id=_chat_id(update),
        text=t("common.reply_keyboard_intro", lang),
        reply_markup=main_reply_keyboard(lang),
    )


def _chat_id(update: Update) -> int:
    """The chat to send a standalone message to: the effective chat, else the
    user's private chat (same id)."""
    chat = update.effective_chat
    return chat.id if chat is not None else require_user(update).id


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = require_message(update)
    user = require_user(update)
    is_new_customer = touch_customer(update)
    user_id = user.id

    if is_new_customer:
        # Nothing else — not even the welcome text — until they've picked a
        # language, since the default 'en' row isn't a real choice yet.
        await show_language_picker(update, context)
        # After the customer's own reply, never before — a slow or failing
        # owner notification must not delay what they see.
        await owner_notify.notify_new_customer(context, user)
        return

    lang = get_lang(user_id)
    text, keyboard = render_main_menu(user_id, lang)
    await message.reply_text(text, reply_markup=keyboard)
    # A message can only carry one reply_markup — the inline main menu above
    # used that slot, so the persistent reply keyboard needs its own message.
    await message.reply_text(
        t("common.reply_keyboard_intro", lang),
        reply_markup=main_reply_keyboard(lang),
    )


async def show_library_stub(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Temporary until the customer library exists (R2)."""
    lang = get_lang(require_user(update).id)
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t("common.back_to_menu_button", lang), callback_data=cb("menu", "show")
                )
            ]
        ]
    )
    await show_screen(update, context, text=t("library.stub", lang), keyboard=keyboard)


async def library_route(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    touch_customer(update)
    await show_library_stub(update, context)


async def menu_show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    touch_customer(update)
    user_id = require_user(update).id
    lang = get_lang(user_id)
    text, keyboard = render_main_menu(user_id, lang)
    await show_screen(update, context, text=text, keyboard=keyboard)


async def lang_route(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from ..keyboards import parse_callback

    query = require_query(update)
    parts = parse_callback(require_data(query), (2, 3))
    if parts is None:
        await query.answer(t("common.expired_menu", DEFAULT_LANGUAGE), show_alert=True)
        return
    action = parts[1]
    if action == "show":
        await show_language_picker(update, context)
    elif action == "set":
        lang = parts[2] if len(parts) > 2 else None
        if lang not in LANGUAGES:
            await query.answer(t("lang.invalid", DEFAULT_LANGUAGE), show_alert=True)
            return
        await set_language(update, context, lang)
    else:
        await query.answer(t("common.expired_menu", DEFAULT_LANGUAGE), show_alert=True)


async def help_placeholder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    touch_customer(update)
    lang = get_lang(require_user(update).id)
    text = t("common.help_text", lang)
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t("common.back_to_menu_button", lang), callback_data=cb("menu", "show")
                )
            ]
        ]
    )
    await show_screen(update, context, text=text, keyboard=keyboard)


async def unknown_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = require_query(update)
    if query.data == "noop":
        await query.answer()
        return
    user = update.effective_user
    user_id = user.id if user else None
    logger.info("Unrecognized callback_data=%r from user_id=%s", query.data, user_id)
    lang = get_lang(user_id) if user_id else DEFAULT_LANGUAGE
    await query.answer(t("common.expired_menu", lang), show_alert=True)
    if query.message:
        text, keyboard = (
            render_main_menu(user_id, lang)
            if user_id
            else (
                t("common.welcome_default", DEFAULT_LANGUAGE),
                InlineKeyboardMarkup([]),
            )
        )
        await context.bot.send_message(
            chat_id=query.message.chat.id, text=text, reply_markup=keyboard
        )
