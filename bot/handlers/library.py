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
from ..i18n import t, tn
from ..keyboards import cb, parse_callback, parse_cb_id
from ..render import esc
from ..repo import orders as orders_repo
from ..security import get_owned_order
from ..tg import require_data, require_query, require_user
from ..ui import show_screen
from ..ui_kit import ICON, SEP_META, SEP_PRICE, btn_label, nav_row, pager, screen, status_icon, trim
from .common import touch_customer

PAGE_SIZE = 5
NAME_BUDGET = 60
BUTTON_WIDTH = 56


def _item_name(item: sqlite3.Row) -> str:
    """Plain (unescaped) display name, each part trimmed."""
    product = trim(item["product_name"], NAME_BUDGET)
    return f"{product}{SEP_PRICE}{trim(item['variant_label'], NAME_BUDGET)}"


def _chip(item: sqlite3.Row) -> tuple[str, str]:
    """(icon, status word) for an item: refunded, being delivered, or delivered."""
    if item["order_status"] == "refunded":
        return status_icon("refunded"), t("library.status_refunded")
    if item["delivered_at"] is None:
        return status_icon("paid"), t("library.being_delivered")
    return status_icon("delivered"), t("library.status_delivered")


def _button(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=data)


async def show_library(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0) -> None:
    touch_customer(update)
    user_id = require_user(update).id

    total = orders_repo.count_library_items(user_id)
    if total == 0:
        keyboard = InlineKeyboardMarkup(
            [[_button(t("common.browse_button"), cb("cat", "cats"))], nav_row()]
        )
        text = screen(ICON.library, t("library.heading"), esc(t("library.empty")))
        await show_screen(update, context, text=text, keyboard=keyboard)
        return

    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    page = max(0, min(page, total_pages - 1))
    items = orders_repo.list_library_items(user_id, PAGE_SIZE, page * PAGE_SIZE)

    rows: list[list[InlineKeyboardButton]] = []
    for item in items:
        icon, word = _chip(item)
        label = btn_label(f"{icon} {_item_name(item)}{SEP_META}{word}", BUTTON_WIDTH)
        rows.append([_button(label, cb("lib", "item", item["id"]))])
    pager_row = pager(page, total_pages, lambda n: cb("lib", "list", n))
    if pager_row:
        rows.append(pager_row)
    rows.append(nav_row())

    text = screen(ICON.library, t("library.heading"), esc(tn("library.count", total)))
    await show_screen(update, context, text=text, keyboard=InlineKeyboardMarkup(rows))


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
    icon, word = _chip(item)
    body = t(
        "library.item_detail",
        name=esc(_item_name(item)),
        id=item["order_id"],
        status=f"{icon} {esc(word)}",
    )
    rows: list[list[InlineKeyboardButton]] = []
    if item["order_status"] != "refunded" and item["delivered_at"] is not None:
        rows.append([_button(t("library.send_button"), cb("lib", "send", item_id))])
    rows.append(nav_row(back=cb("lib", "list", 0)))
    text = screen(ICON.library, t("library.item_title"), body, crumbs=[t("library.heading")])
    await show_screen(update, context, text=text, keyboard=InlineKeyboardMarkup(rows))


async def send_item(update: Update, context: ContextTypes.DEFAULT_TYPE, item_id: int) -> None:
    query = require_query(update)
    item = await _owned_item(update, item_id)
    if item is None:
        return
    if item["order_status"] == "refunded" or item["delivered_at"] is None:
        await query.answer(t("library.unavailable"), show_alert=True)
        return
    if await delivery.deliver_item(context.bot, item_id):
        await query.answer(t("library.resent"))
    else:
        await query.answer(t("library.resend_failed"), show_alert=True)


async def library_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/library: the same screen as the My Library button."""
    await show_library(update, context, 0)


async def route(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = require_query(update)
    parts = parse_callback(require_data(query), 3)
    value = parse_cb_id(parts[2]) if parts else None
    if parts is None or value is None:
        await query.answer(t("common.expired_menu"), show_alert=True)
        return
    action = parts[1]
    if action == "list":
        await show_library(update, context, value)
    elif action == "item":
        await show_item(update, context, value)
    elif action == "send":
        await send_item(update, context, value)
    else:
        await query.answer(t("common.expired_menu"), show_alert=True)
