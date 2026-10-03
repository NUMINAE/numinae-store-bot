"""Cart screen: view, adjust, remove, clear (with confirmation). The Checkout
button hands over to handlers/checkout ("co:start"). Keyed on variant_id
throughout. Registered under the "cart:" callback namespace and routed by action word.

Quantity limits live in repo/cart.py; this module only surfaces them: the "+"
button is hidden at the cap (a stale one alerts), and a line the repo removes
because its stock hit zero is announced on the cart screen, never silently."""

from __future__ import annotations

import sqlite3

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from ..i18n import t
from ..keyboards import cb, parse_callback, parse_cb_id
from ..limits import MAX_ORDER_STARS
from ..render import bold, esc, stars, strike
from ..repo import cart
from ..repo import products as products_repo
from ..tg import require_data, require_query, require_user
from ..ui import show_screen
from .common import get_lang, touch_customer

_NAME_TRUNCATE = 22


def _truncate(name: str) -> str:
    return name if len(name) <= _NAME_TRUNCATE else name[: _NAME_TRUNCATE - 1] + "…"


def _line_name(line: sqlite3.Row) -> str:
    product_name = line["product_name"] or "Unknown item"
    variant_label = line["variant_label"] or ""
    return f"{product_name} — {variant_label}" if variant_label else product_name


def _line_buttons(line: sqlite3.Row, display_name: str) -> list[InlineKeyboardButton]:
    qty = line["quantity"]
    vid = line["variant_id"]
    limit = cart.max_quantity_for(line["delivery_kind"], line["key_stock"])
    name_button = InlineKeyboardButton(f"{_truncate(display_name)} × {qty}", callback_data="noop")
    remove_button = InlineKeyboardButton("✕", callback_data=cb("cart", "remove", vid))
    if line["delivery_kind"] != "key":
        return [name_button, remove_button]  # one per customer: nothing to step
    row = [
        InlineKeyboardButton("−", callback_data=cb("cart", "dec", vid, qty)),
        name_button,
    ]
    if qty < limit:
        row.append(InlineKeyboardButton("+", callback_data=cb("cart", "inc", vid, qty)))
    row.append(remove_button)
    return row


async def show_cart(
    update: Update, context: ContextTypes.DEFAULT_TYPE, notice: str | None = None
) -> None:
    touch_customer(update)
    user_id = require_user(update).id
    lang = get_lang(user_id)
    lines = cart.list_lines(user_id)
    notice_block = [notice, ""] if notice else []

    if not lines:
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        t("common.browse_button", lang), callback_data=cb("cat", "cats")
                    )
                ],
                [
                    InlineKeyboardButton(
                        t("common.back_to_menu_button", lang), callback_data=cb("menu", "show")
                    )
                ],
            ]
        )
        text = "\n".join([*notice_block, t("cart.empty", lang)])
        await show_screen(update, context, text=text, keyboard=keyboard)
        return

    body = [*notice_block, bold(t("cart.heading", lang)), ""]
    buttons = []
    subtotal = 0

    for line in lines:
        display_name = _line_name(line)
        qty = line["quantity"]

        if products_repo.is_variant_available(line):
            line_total = line["line_total"]
            subtotal += line_total
            body.append(f"{esc(display_name)} × {qty} — {stars(line_total)}")
            buttons.append(_line_buttons(line, display_name))
        else:
            body.append(f"{strike(display_name)}{t('cart.line_unavailable_suffix', lang)}")
            buttons.append(
                [
                    InlineKeyboardButton(
                        t("cart.remove_button", lang, name=_truncate(display_name)),
                        callback_data=cb("cart", "remove", line["variant_id"]),
                    )
                ]
            )

    body.append("")
    body.append(bold(t("cart.subtotal", lang, amount=stars(subtotal))))
    if subtotal > MAX_ORDER_STARS:
        body.append(t("cart.over_limit_warning", lang, limit=MAX_ORDER_STARS))
    text = "\n".join(body)

    buttons.append(
        [
            InlineKeyboardButton(t("cart.checkout_button", lang), callback_data=cb("co", "start")),
            InlineKeyboardButton(
                t("cart.clear_cart_button", lang), callback_data=cb("cart", "clear", "ask")
            ),
        ]
    )
    buttons.append(
        [
            InlineKeyboardButton(
                t("catalogue.keep_browsing_button", lang), callback_data=cb("cat", "cats")
            ),
            InlineKeyboardButton(
                t("common.back_to_menu_button", lang), callback_data=cb("menu", "show")
            ),
        ]
    )

    await show_screen(update, context, text=text, keyboard=InlineKeyboardMarkup(buttons))


async def _expired(update: Update) -> None:
    lang = get_lang(require_user(update).id)
    await require_query(update).answer(t("common.expired_menu", lang), show_alert=True)


async def _set_line_qty(update: Update, context: ContextTypes.DEFAULT_TYPE, vid: int, target: int):
    """Apply a stepper change. If the repo removes the line although a positive
    quantity was asked for, its stock hit zero — say so on the cart screen."""
    user_id = require_user(update).id
    lang = get_lang(user_id)
    variant = products_repo.get_variant(vid)
    effective = cart.set_qty(user_id, vid, target)
    notice = None
    if effective == 0 and target > 0:
        name = (
            f"{variant['product_name']} — {variant['variant_label']}"
            if variant is not None
            else "An item"
        )
        notice = t("cart.removed_out_of_stock", lang, name=esc(name))
    await show_cart(update, context, notice=notice)


async def cart_inc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = require_query(update)
    lang = get_lang(require_user(update).id)
    parts = parse_callback(require_data(query), 4)
    vid = parse_cb_id(parts[2]) if parts else None
    qty = parse_cb_id(parts[3]) if parts else None
    if vid is None or qty is None:
        await _expired(update)
        return
    limit = cart.max_quantity(vid)
    if limit > 0 and qty >= limit:
        await query.answer(t("catalogue.max_qty_alert", lang, max=limit), show_alert=True)
        return
    await _set_line_qty(update, context, vid, qty + 1)


async def cart_dec(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    parts = parse_callback(require_data(require_query(update)), 4)
    vid = parse_cb_id(parts[2]) if parts else None
    qty = parse_cb_id(parts[3]) if parts else None
    if vid is None or qty is None:
        await _expired(update)
        return
    await _set_line_qty(update, context, vid, qty - 1)


async def cart_remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    parts = parse_callback(require_data(require_query(update)), 3)
    vid = parse_cb_id(parts[2]) if parts else None
    if vid is None:
        await _expired(update)
        return
    cart.remove(require_user(update).id, vid)
    await show_cart(update, context)


async def cart_clear_ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    touch_customer(update)
    user_id = require_user(update).id
    lang = get_lang(user_id)
    lines = cart.list_lines(user_id)
    total_qty = sum(line["quantity"] for line in lines)
    if total_qty == 0:
        await show_cart(update, context)
        return
    noun = t("cart.item_singular", lang) if total_qty == 1 else t("cart.item_plural", lang)
    text = t("cart.clear_confirm", lang, count=total_qty, noun=noun)
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t("cart.yes_clear_button", lang), callback_data=cb("cart", "clear", "yes")
                ),
                InlineKeyboardButton(
                    t("cart.no_button", lang), callback_data=cb("cart", "clear", "no")
                ),
            ]
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
    parts = parse_callback(require_data(query), range(2, 5))
    if parts is None:
        await _expired(update)
        return
    action = parts[1]
    if action == "view":
        await show_cart(update, context)
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
