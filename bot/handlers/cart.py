"""Cart screen: view, adjust, remove, clear (with confirmation), and the
checkout stub. Keyed on variant_id throughout. Registered under the "cart:"
callback namespace and routed by action word."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from ..i18n import t
from ..keyboards import cb, parse_callback, parse_cb_id
from ..render import bold, esc, strike, usd
from ..repo import cart
from ..repo import products as products_repo
from ..ui import show_screen
from . import checkout
from .common import get_lang, touch_customer

MAX_QTY = 99
_NAME_TRUNCATE = 22


def _truncate(name: str) -> str:
    return name if len(name) <= _NAME_TRUNCATE else name[: _NAME_TRUNCATE - 1] + "…"


def _line_name(line) -> str:
    product_name = line["product_name"] or "Unknown item"
    variant_label = line["variant_label"] or ""
    return f"{product_name} {variant_label}".strip()


async def show_cart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    touch_customer(update)
    user_id = update.effective_user.id
    lang = get_lang(user_id)
    lines = cart.list_lines(user_id)

    if not lines:
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        t("common.browse_teas_button", lang), callback_data=cb("cat", "cats")
                    )
                ],
                [
                    InlineKeyboardButton(
                        t("common.back_to_menu_button", lang), callback_data=cb("menu", "show")
                    )
                ],
            ]
        )
        await show_screen(update, context, text=t("cart.empty", lang), keyboard=keyboard)
        return

    body = [bold(t("cart.heading", lang)), ""]
    buttons = []
    subtotal = 0

    for line in lines:
        available = products_repo.is_variant_available(line)
        display_name = _line_name(line)
        qty = line["quantity"]

        if available:
            line_total = line["line_total"]
            subtotal += line_total
            body.append(f"{esc(display_name)} × {qty} — {usd(line_total)}")
            buttons.append(
                [
                    InlineKeyboardButton(
                        "−", callback_data=cb("cart", "dec", line["variant_id"], qty)
                    ),
                    InlineKeyboardButton(
                        f"{_truncate(display_name)} × {qty}", callback_data="noop"
                    ),
                    InlineKeyboardButton(
                        "+", callback_data=cb("cart", "inc", line["variant_id"], qty)
                    ),
                    InlineKeyboardButton(
                        "✕", callback_data=cb("cart", "remove", line["variant_id"])
                    ),
                ]
            )
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
    body.append(bold(t("cart.subtotal", lang, amount=usd(subtotal))))
    text = "\n".join(body)

    buttons.append(
        [
            InlineKeyboardButton(
                t("cart.checkout_button", lang), callback_data=cb("cart", "checkout")
            ),
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


async def cart_inc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = get_lang(update.effective_user.id)
    parts = parse_callback(update.callback_query.data, 4)
    vid = parse_cb_id(parts[2]) if parts else None
    qty = parse_cb_id(parts[3]) if parts else None
    if vid is None or qty is None:
        await update.callback_query.answer(t("common.expired_menu", lang), show_alert=True)
        return
    new_qty = min(MAX_QTY, qty + 1)
    if new_qty == qty:
        await update.callback_query.answer(
            t("catalogue.max_qty_alert", lang, max=MAX_QTY), show_alert=True
        )
        return
    cart.set_qty(update.effective_user.id, vid, new_qty)
    await show_cart(update, context)


async def cart_dec(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = get_lang(update.effective_user.id)
    parts = parse_callback(update.callback_query.data, 4)
    vid = parse_cb_id(parts[2]) if parts else None
    qty = parse_cb_id(parts[3]) if parts else None
    if vid is None or qty is None:
        await update.callback_query.answer(t("common.expired_menu", lang), show_alert=True)
        return
    cart.set_qty(update.effective_user.id, vid, qty - 1)
    await show_cart(update, context)


async def cart_remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = get_lang(update.effective_user.id)
    parts = parse_callback(update.callback_query.data, 3)
    vid = parse_cb_id(parts[2]) if parts else None
    if vid is None:
        await update.callback_query.answer(t("common.expired_menu", lang), show_alert=True)
        return
    cart.remove(update.effective_user.id, vid)
    await show_cart(update, context)


async def cart_clear_ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    touch_customer(update)
    user_id = update.effective_user.id
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
    cart.clear(update.effective_user.id)
    await show_cart(update, context)


async def cart_clear_no(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_cart(update, context)


async def cart_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await checkout.entry(update, context)


async def route(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    lang = get_lang(update.effective_user.id) if update.effective_user else "en"
    parts = parse_callback(query.data, range(2, 5))
    if parts is None:
        await query.answer(t("common.expired_menu", lang), show_alert=True)
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
            await query.answer(t("common.expired_menu", lang), show_alert=True)
            return
        sub = parts[2]
        if sub == "ask":
            await cart_clear_ask(update, context)
        elif sub == "yes":
            await cart_clear_yes(update, context)
        else:
            await cart_clear_no(update, context)
    elif action == "checkout":
        await cart_checkout(update, context)
    else:
        await query.answer(t("common.expired_menu", lang), show_alert=True)
