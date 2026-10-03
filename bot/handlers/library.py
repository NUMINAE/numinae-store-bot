"""My Library: the customer's purchased items. Registered under the "lib:"
callback namespace — lib:list:{page}, lib:item:{item_id}, lib:send:{item_id}.

Every item is reached through its order and get_owned_order, so a nonexistent
item and someone else's item produce the IDENTICAL response (the generic
"no longer available" alert) and the id space can't be probed."""

from __future__ import annotations

import sqlite3

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from .. import delivery
from ..i18n import t
from ..keyboards import cb, parse_callback, parse_cb_id
from ..render import bold, esc
from ..repo import orders as orders_repo
from ..security import get_owned_order
from ..tg import require_data, require_query, require_user
from ..ui import show_screen
from .common import get_lang, touch_customer

PAGE_SIZE = 5
_NAME_TRUNCATE = 40


def _item_name(item: sqlite3.Row) -> str:
    return f"{item['product_name']} — {item['variant_label']}"


def _status_label(item: sqlite3.Row, lang: str) -> str:
    if item["order_status"] == "refunded":
        return t("library.status_refunded", lang)
    if item["delivered_at"] is None:
        return t("library.being_delivered", lang)
    return t("library.status_delivered", lang)


def _button(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=data)


async def show_library(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0) -> None:
    touch_customer(update)
    user_id = require_user(update).id
    lang = get_lang(user_id)

    total = orders_repo.count_library_items(user_id)
    if total == 0:
        keyboard = InlineKeyboardMarkup(
            [
                [_button(t("common.browse_button", lang), cb("cat", "cats"))],
                [_button(t("common.back_to_menu_button", lang), cb("menu", "show"))],
            ]
        )
        await show_screen(update, context, text=t("library.empty", lang), keyboard=keyboard)
        return

    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    page = max(0, min(page, total_pages - 1))
    items = orders_repo.list_library_items(user_id, PAGE_SIZE, page * PAGE_SIZE)

    rows: list[list[InlineKeyboardButton]] = []
    for item in items:
        name = _item_name(item)
        if len(name) > _NAME_TRUNCATE:
            name = name[: _NAME_TRUNCATE - 1] + "…"
        label = f"{name} · {_status_label(item, lang)}"
        rows.append([_button(label, cb("lib", "item", item["id"]))])

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(_button("◀", cb("lib", "list", page - 1)))
    if total_pages > 1:
        nav.append(_button(f"Page {page + 1}/{total_pages}", "noop"))
    if page < total_pages - 1:
        nav.append(_button("▶", cb("lib", "list", page + 1)))
    if nav:
        rows.append(nav)
    rows.append([_button(t("common.back_to_menu_button", lang), cb("menu", "show"))])

    await show_screen(
        update,
        context,
        text=bold(t("library.heading", lang)),
        keyboard=InlineKeyboardMarkup(rows),
    )


async def _owned_item(update: Update, item_id: int) -> sqlite3.Row | None:
    """The item if it belongs to the caller. A nonexistent item is checked
    against order id 0 (which never exists), so both failures share one path
    and one response."""
    item = orders_repo.get_item_with_order(item_id)
    order = await get_owned_order(update, item["order_id"] if item is not None else 0)
    if order is None or item is None:
        return None
    return item


async def show_item(update: Update, context: ContextTypes.DEFAULT_TYPE, item_id: int) -> None:
    touch_customer(update)
    item = await _owned_item(update, item_id)
    if item is None:
        return
    lang = get_lang(require_user(update).id)
    text = t(
        "library.item_detail",
        lang,
        name=bold(_item_name(item)),
        id=item["order_id"],
        status=esc(_status_label(item, lang)),
    )
    rows: list[list[InlineKeyboardButton]] = []
    if item["order_status"] != "refunded" and item["delivered_at"] is not None:
        rows.append([_button(t("library.send_button", lang), cb("lib", "send", item_id))])
    rows.append([_button(t("library.back_button", lang), cb("lib", "list", 0))])
    await show_screen(update, context, text=text, keyboard=InlineKeyboardMarkup(rows))


async def send_item(update: Update, context: ContextTypes.DEFAULT_TYPE, item_id: int) -> None:
    query = require_query(update)
    item = await _owned_item(update, item_id)
    if item is None:
        return
    lang = get_lang(require_user(update).id)
    if item["order_status"] == "refunded" or item["delivered_at"] is None:
        await query.answer(t("library.unavailable", lang), show_alert=True)
        return
    if await delivery.deliver_item(context.bot, item_id):
        await query.answer(t("library.resent", lang))
    else:
        await query.answer(t("library.resend_failed", lang), show_alert=True)


async def route(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = require_query(update)
    lang = get_lang(require_user(update).id)
    parts = parse_callback(require_data(query), 3)
    value = parse_cb_id(parts[2]) if parts else None
    if parts is None or value is None:
        await query.answer(t("common.expired_menu", lang), show_alert=True)
        return
    action = parts[1]
    if action == "list":
        await show_library(update, context, value)
    elif action == "item":
        await show_item(update, context, value)
    elif action == "send":
        await send_item(update, context, value)
    else:
        await query.answer(t("common.expired_menu", lang), show_alert=True)
