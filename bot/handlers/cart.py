"""Cart screen: view (paged), adjust, remove, clear (with confirmation). The
Checkout button hands over to handlers/checkout ("co:start"). Keyed on
variant_id throughout. Registered under the "cart:" callback namespace.

Quantity limits live in repo/cart.py; this module only surfaces them: the "+"
button is hidden at the cap (a stale one alerts), and a line the repo removes
because its stock hit zero is announced on the cart screen, never silently.

The cart shows CART_PAGE_SIZE lines per page so even a cart of maximum-length,
fully-escaped names stays under Telegram's 4096-character limit."""

from __future__ import annotations

import sqlite3

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from ..i18n import t, tn
from ..keyboards import cb, parse_callback, parse_cb_id
from ..limits import MAX_ORDER_STARS
from ..render import bold, esc, stars, strike
from ..repo import cart
from ..repo import products as products_repo
from ..tg import require_data, require_query, require_user
from ..ui import show_screen
from ..ui_kit import ICON, SEP_PRICE, btn_label, nav_row, pager, screen, trim
from .common import touch_customer

CART_PAGE_SIZE = 5
NAME_BUDGET = 60
BUTTON_NAME_WIDTH = 22


def _line_name(line: sqlite3.Row) -> str:
    """Plain (unescaped) display name, each part trimmed."""
    product_name = trim(line["product_name"] or "Unknown item", NAME_BUDGET)
    variant_label = trim(line["variant_label"] or "", NAME_BUDGET)
    return f"{product_name}{SEP_PRICE}{variant_label}" if variant_label else product_name


def _line_buttons(line: sqlite3.Row, display_name: str, page: int) -> list[InlineKeyboardButton]:
    qty = line["quantity"]
    vid = line["variant_id"]
    limit = cart.max_quantity_for(line["delivery_kind"], line["key_stock"])
    name_button = InlineKeyboardButton(
        f"{btn_label(display_name, BUTTON_NAME_WIDTH)} × {qty}", callback_data="noop"
    )
    remove_button = InlineKeyboardButton(ICON.remove, callback_data=cb("cart", "remove", vid, page))
    if line["delivery_kind"] != "key":
        return [name_button, remove_button]  # one per customer: nothing to step
    row = [
        InlineKeyboardButton(ICON.minus, callback_data=cb("cart", "dec", vid, qty, page)),
        name_button,
    ]
    if qty < limit:
        row.append(InlineKeyboardButton(ICON.plus, callback_data=cb("cart", "inc", vid, qty, page)))
    row.append(remove_button)
    return row


async def show_cart(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    notice: str | None = None,
    page: int = 0,
) -> None:
    touch_customer(update)
    user_id = require_user(update).id
    lines = cart.list_lines(user_id)
    notice_block = [esc(notice), ""] if notice else []

    if not lines:
        keyboard = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(t("common.browse_button"), callback_data=cb("cat", "cats"))],
                nav_row(),
            ]
        )
        empty_body = "\n".join([*notice_block, esc(t("cart.empty")), esc(t("cart.empty_next"))])
        await show_screen(
            update,
            context,
            text=screen(ICON.cart, t("cart.heading"), empty_body),
            keyboard=keyboard,
        )
        return

    # The subtotal always covers every purchasable line, whatever page is shown.
    subtotal = sum(line["line_total"] for line in lines if products_repo.is_variant_available(line))
    total_pages = (len(lines) + CART_PAGE_SIZE - 1) // CART_PAGE_SIZE
    page = max(0, min(page, total_pages - 1))
    visible = lines[page * CART_PAGE_SIZE : (page + 1) * CART_PAGE_SIZE]

    body = list(notice_block)
    buttons: list[list[InlineKeyboardButton]] = []
    for line in visible:
        display_name = _line_name(line)
        qty = line["quantity"]
        if products_repo.is_variant_available(line):
            body.append(f"{esc(display_name)} × {qty}{SEP_PRICE}{stars(line['line_total'])}")
            buttons.append(_line_buttons(line, display_name, page))
        else:
            body.append(f"{strike(display_name)}{esc(t('cart.line_unavailable_suffix'))}")
            buttons.append(
                [
                    InlineKeyboardButton(
                        t("cart.remove_button", name=btn_label(display_name, BUTTON_NAME_WIDTH)),
                        callback_data=cb("cart", "remove", line["variant_id"], page),
                    )
                ]
            )

    body += ["", bold(t("cart.subtotal", amount=stars(subtotal)))]
    if subtotal > MAX_ORDER_STARS:
        body.append(t("cart.over_limit_warning", limit=MAX_ORDER_STARS))

    pager_row = pager(page, total_pages, lambda n: cb("cart", "view", n))
    if pager_row:
        buttons.append(pager_row)
    buttons.append(
        [
            InlineKeyboardButton(t("cart.checkout_button"), callback_data=cb("co", "start")),
            InlineKeyboardButton(
                t("cart.clear_cart_button"), callback_data=cb("cart", "clear", "ask")
            ),
        ]
    )
    buttons.append(
        [InlineKeyboardButton(t("catalogue.keep_browsing_button"), callback_data=cb("cat", "cats"))]
    )
    buttons.append(nav_row())

    text = screen(ICON.cart, t("cart.heading"), "\n".join(body))
    await show_screen(update, context, text=text, keyboard=InlineKeyboardMarkup(buttons))


async def _expired(update: Update) -> None:
    await require_query(update).answer(t("common.expired_menu"), show_alert=True)


def _page_arg(parts: list[str], index: int) -> int | None:
    """The optional trailing page segment (default 0); None if it is malformed."""
    return parse_cb_id(parts[index]) if len(parts) > index else 0


async def _set_line_qty(
    update: Update, context: ContextTypes.DEFAULT_TYPE, vid: int, target: int, page: int
) -> None:
    """Apply a stepper change. If the repo removes the line although a positive
    quantity was asked for, its stock hit zero — say so on the cart screen."""
    user_id = require_user(update).id
    variant = products_repo.get_variant(vid)
    effective = cart.set_qty(user_id, vid, target)
    notice = None
    if effective == 0 and target > 0:
        name = (
            f"{variant['product_name']} — {variant['variant_label']}"
            if variant is not None
            else "An item"
        )
        notice = t("cart.removed_out_of_stock", name=trim(name, 2 * NAME_BUDGET + 3))
    await show_cart(update, context, notice=notice, page=page)


async def cart_inc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = require_query(update)
    parts = parse_callback(require_data(query), (4, 5))
    vid = parse_cb_id(parts[2]) if parts else None
    qty = parse_cb_id(parts[3]) if parts else None
    page = _page_arg(parts, 4) if parts else None
    if vid is None or qty is None or page is None:
        await _expired(update)
        return
    limit = cart.max_quantity(vid)
    if limit > 0 and qty >= limit:
        await query.answer(t("catalogue.max_qty_alert", max=limit), show_alert=True)
        return
    await _set_line_qty(update, context, vid, qty + 1, page)


async def cart_dec(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    parts = parse_callback(require_data(require_query(update)), (4, 5))
    vid = parse_cb_id(parts[2]) if parts else None
    qty = parse_cb_id(parts[3]) if parts else None
    page = _page_arg(parts, 4) if parts else None
    if vid is None or qty is None or page is None:
        await _expired(update)
        return
    await _set_line_qty(update, context, vid, qty - 1, page)


async def cart_remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    parts = parse_callback(require_data(require_query(update)), (3, 4))
    vid = parse_cb_id(parts[2]) if parts else None
    page = _page_arg(parts, 3) if parts else None
    if vid is None or page is None:
        await _expired(update)
        return
    cart.remove(require_user(update).id, vid)
    await show_cart(update, context, page=page)


async def cart_clear_ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    touch_customer(update)
    user_id = require_user(update).id
    lines = cart.list_lines(user_id)
    total_qty = sum(line["quantity"] for line in lines)
    if total_qty == 0:
        await show_cart(update, context)
        return
    text = screen(ICON.warning, t("cart.clear_title"), esc(tn("cart.clear_confirm", total_qty)))
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t("cart.yes_clear_button"), callback_data=cb("cart", "clear", "yes")
                ),
                InlineKeyboardButton(t("cart.no_button"), callback_data=cb("cart", "clear", "no")),
            ],
            nav_row(back=cb("cart", "view")),
        ]
    )
    await show_screen(update, context, text=text, keyboard=keyboard)


async def cart_clear_yes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cart.clear(require_user(update).id)
    await show_cart(update, context)


async def cart_clear_no(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_cart(update, context)


async def route(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = require_query(update)
    parts = parse_callback(require_data(query), range(2, 6))
    if parts is None:
        await _expired(update)
        return
    action = parts[1]
    if action == "view":
        page = _page_arg(parts, 2) if len(parts) == 3 else 0 if len(parts) == 2 else None
        if page is None:
            await _expired(update)
            return
        await show_cart(update, context, page=page)
    elif action == "inc":
        await cart_inc(update, context)
    elif action == "dec":
        await cart_dec(update, context)
    elif action == "remove":
        await cart_remove(update, context)
    elif action == "clear":
        if len(parts) < 3:
            await _expired(update)
            return
        sub = parts[2]
        if sub == "ask":
            await cart_clear_ask(update, context)
        elif sub == "yes":
            await cart_clear_yes(update, context)
        else:
            await cart_clear_no(update, context)
    else:
        await _expired(update)
