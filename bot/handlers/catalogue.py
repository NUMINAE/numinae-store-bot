"""Catalogue browsing: category list -> product list -> product card (editions)
-> edition screen -> add to cart. Registered under the "cat:" callback
namespace and routed by action word.

Every screen is ui_kit.screen() text plus a keyboard that ends with nav_row().
Admin-entered text (names, descriptions) is trimmed raw and escaped once."""

from __future__ import annotations

import sqlite3

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from ..i18n import t, tn
from ..keyboards import cb, parse_callback, parse_cb_id
from ..render import esc, stars
from ..repo import cart, categories
from ..repo import products as products_repo
from ..tg import require_data, require_query, require_user
from ..ui import show_screen
from ..ui_kit import (
    ICON,
    SEP_PRICE,
    btn_label,
    delivery_icon,
    fit_lines,
    nav_row,
    pager,
    screen,
    trim,
    trim_escaped,
)
from .common import touch_customer

PAGE_SIZE = 5
MIN_QTY = 1
MAX_EDITIONS_SHOWN_CAP = 40  # keeps the keyboard well inside Telegram's limits
DESCRIPTION_BUDGET = 900  # escaped characters
EDITION_LINES_BUDGET = 1600  # escaped characters
NAME_BUDGET = 60
BUTTON_WIDTH = 56


def delivery_label(delivery_kind: str) -> str:
    return t(f"delivery.{delivery_kind}")


def _cart_label(user_id: int) -> str:
    item_count, _ = cart.summary(user_id)
    return t("common.my_cart_count", count=item_count) if item_count else t("common.my_cart_button")


def _cart_button(user_id: int) -> InlineKeyboardButton:
    return InlineKeyboardButton(_cart_label(user_id), callback_data=cb("cart", "view"))


def unavailable_alert(variant: sqlite3.Row | None) -> str:
    """Why a customer can't buy this edition: sold out (a key edition whose
    stock ran out while everything above it is active) or simply gone."""
    if (
        variant is not None
        and variant["delivery_kind"] == "key"
        and variant["key_stock"] == 0
        and variant["variant_is_active"]
        and variant["product_is_active"]
        and variant["category_is_active"]
    ):
        return t("catalogue.out_of_stock_alert")
    return t("catalogue.item_unavailable_alert")


def _is_stepper_edition(variant: sqlite3.Row) -> bool:
    """Only key editions have a quantity stepper; file and text editions are
    one per customer."""
    return bool(variant["delivery_kind"] == "key")


def _edition_button_label(v: sqlite3.Row) -> str:
    label = f"{trim(v['label'], NAME_BUDGET)}{SEP_PRICE}{stars(v['price_stars'])}"
    return btn_label(label, BUTTON_WIDTH)


# ---------------------------------------------------------------------------
# Category list
# ---------------------------------------------------------------------------


async def show_categories(
    update: Update, context: ContextTypes.DEFAULT_TYPE, alert: str | None = None
) -> None:
    touch_customer(update)
    user_id = require_user(update).id

    cats = categories.list_active()
    if not cats:
        text = screen(ICON.browse, t("catalogue.heading"), esc(t("catalogue.no_stock")))
        keyboard = InlineKeyboardMarkup([nav_row()])
        await show_screen(update, context, text=text, keyboard=keyboard, alert=alert)
        return

    buttons = [
        [
            InlineKeyboardButton(
                btn_label(c["name"], BUTTON_WIDTH), callback_data=cb("cat", "cat", c["id"], 0)
            )
        ]
        for c in cats
    ]
    buttons.append([_cart_button(user_id)])
    buttons.append(nav_row())
    text = screen(ICON.browse, t("catalogue.heading"), esc(t("catalogue.choose_category")))
    await show_screen(
        update, context, text=text, keyboard=InlineKeyboardMarkup(buttons), alert=alert
    )


# ---------------------------------------------------------------------------
# Product list (within a category)
# ---------------------------------------------------------------------------


async def show_products(
    update: Update, context: ContextTypes.DEFAULT_TYPE, category_id: int, page: int
) -> None:
    touch_customer(update)
    user_id = require_user(update).id

    category = categories.get(category_id)
    if category is None or not category["is_active"]:
        await show_categories(update, context)
        return

    total = products_repo.count_active_in_category(category_id)
    if total == 0:
        await show_categories(update, context)
        return

    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    page = max(0, min(page, total_pages - 1))
    rows = products_repo.list_active_in_category(category_id, PAGE_SIZE, page * PAGE_SIZE)

    buttons = []
    for p in rows:
        price_hint = (
            t("catalogue.price_from", price=stars(p["min_price_stars"]))
            if p["min_price_stars"] is not None
            else ""
        )
        label = btn_label(f"{trim(p['name'], NAME_BUDGET)}{price_hint}", BUTTON_WIDTH)
        buttons.append([InlineKeyboardButton(label, callback_data=cb("cat", "p", p["id"], page))])
    pager_row = pager(page, total_pages, lambda n: cb("cat", "cat", category_id, n))
    if pager_row:
        buttons.append(pager_row)
    buttons.append([_cart_button(user_id)])
    buttons.append(nav_row(back=cb("cat", "cats")))

    text = screen(
        ICON.browse,
        trim(category["name"], NAME_BUDGET),
        esc(t("catalogue.tap_to_view")),
        crumbs=[t("catalogue.heading")],
    )
    await show_screen(update, context, text=text, keyboard=InlineKeyboardMarkup(buttons))


# ---------------------------------------------------------------------------
# Product card (edition buttons)
# ---------------------------------------------------------------------------


async def show_product_card(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    product_id: int,
    page: int,
    alert: str | None = None,
) -> None:
    touch_customer(update)
    user_id = require_user(update).id

    product = products_repo.get(product_id)
    if product is None:
        await show_categories(update, context, alert=alert)
        return

    category = categories.get(product["category_id"])
    all_editions = products_repo.list_variants(product_id)
    total_editions = len(all_editions)
    editions = all_editions[:MAX_EDITIONS_SHOWN_CAP]
    edition_lines = [
        f"{delivery_icon(v['delivery_kind'])} {esc(trim(v['label'], NAME_BUDGET))}"
        f"{SEP_PRICE}{stars(v['price_stars'])}"
        for v in editions
    ]
    line_texts, _ = fit_lines(edition_lines, EDITION_LINES_BUDGET)
    shown = editions[: len(line_texts)]

    parts: list[str] = []
    if product["description"]:
        parts.append(esc(trim_escaped(product["description"], DESCRIPTION_BUDGET)))
    if not editions:
        parts.append(esc(t("catalogue.no_editions")))
    else:
        parts.append("\n".join(line_texts))
        if total_editions > len(shown):
            parts.append(esc(tn("catalogue.more_editions", total_editions - len(shown))))
    crumbs = [t("catalogue.heading")]
    if category is not None:
        crumbs.append(trim(category["name"], NAME_BUDGET))
    text = screen(
        ICON.browse, trim(product["name"], NAME_BUDGET), "\n\n".join(parts), crumbs=crumbs
    )

    buttons = [
        [
            InlineKeyboardButton(
                _edition_button_label(v), callback_data=cb("cat", "v", v["id"], page)
            )
        ]
        for v in shown
    ]
    buttons.append([_cart_button(user_id)])
    buttons.append(nav_row(back=cb("cat", "cat", product["category_id"], page)))
    await show_screen(
        update, context, text=text, keyboard=InlineKeyboardMarkup(buttons), alert=alert
    )


# ---------------------------------------------------------------------------
# Edition screen (key editions get a quantity stepper)
# ---------------------------------------------------------------------------


async def show_variant_screen(
    update: Update, context: ContextTypes.DEFAULT_TYPE, variant_id: int, page: int, qty: int
) -> None:
    touch_customer(update)
    user_id = require_user(update).id

    variant = products_repo.get_variant(variant_id)
    if variant is None or not products_repo.is_variant_available(variant):
        alert = unavailable_alert(variant)
        if variant is not None and variant["product_is_active"]:
            await show_product_card(update, context, variant["product_id"], page, alert=alert)
        else:
            await show_categories(update, context, alert=alert)
        return

    icon = delivery_icon(variant["delivery_kind"])
    lines = [
        t("catalogue.edition_label", label=esc(trim(variant["variant_label"], NAME_BUDGET))),
        t("catalogue.price_label", price=stars(variant["price_stars"])),
        f"{icon} " + t("catalogue.delivery_line", kind=delivery_label(variant["delivery_kind"])),
    ]
    rows: list[list[InlineKeyboardButton]] = []
    add_qty = MIN_QTY
    if _is_stepper_edition(variant):
        limit = cart.max_quantity_for(variant["delivery_kind"], variant["key_stock"])
        qty = max(MIN_QTY, min(qty, limit))
        add_qty = qty
        total = esc(
            t("catalogue.total_for_qty", qty=qty, total=stars(variant["price_stars"] * qty))
        )
        lines += ["", f"<b>{total}</b>"]
        rows.append(
            [
                InlineKeyboardButton(
                    ICON.minus, callback_data=cb("cat", "q", variant_id, page, qty, "dec")
                ),
                InlineKeyboardButton(str(qty), callback_data="noop"),
                InlineKeyboardButton(
                    ICON.plus, callback_data=cb("cat", "q", variant_id, page, qty, "inc")
                ),
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                t("catalogue.add_to_cart_button"),
                callback_data=cb("cat", "add", variant_id, page, add_qty),
            )
        ]
    )
    rows.append([_cart_button(user_id)])
    rows.append(nav_row(back=cb("cat", "p", variant["product_id"], page)))
    text = screen(
        icon,
        trim(variant["product_name"], NAME_BUDGET),
        "\n".join(lines),
        crumbs=[t("catalogue.heading"), trim(variant["category_name"], NAME_BUDGET)],
    )
    await show_screen(update, context, text=text, keyboard=InlineKeyboardMarkup(rows))


# ---------------------------------------------------------------------------
# Callback handlers
# ---------------------------------------------------------------------------


async def _expired(update: Update) -> None:
    await require_query(update).answer(t("common.expired_menu"), show_alert=True)


async def cat_cat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    parts = parse_callback(require_data(require_query(update)), 4)
    category_id = parse_cb_id(parts[2]) if parts else None
    page = parse_cb_id(parts[3]) if parts else None
    if category_id is None or page is None:
        await _expired(update)
        return
    await show_products(update, context, category_id, page)


async def cat_p(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    parts = parse_callback(require_data(require_query(update)), 4)
    product_id = parse_cb_id(parts[2]) if parts else None
    page = parse_cb_id(parts[3]) if parts else None
    if product_id is None or page is None:
        await _expired(update)
        return
    await show_product_card(update, context, product_id, page)


async def cat_v(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    parts = parse_callback(require_data(require_query(update)), 4)
    variant_id = parse_cb_id(parts[2]) if parts else None
    page = parse_cb_id(parts[3]) if parts else None
    if variant_id is None or page is None:
        await _expired(update)
        return
    await show_variant_screen(update, context, variant_id, page, qty=MIN_QTY)


async def cat_q(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = require_query(update)
    parts = parse_callback(require_data(query), 6)
    if parts is None:
        await _expired(update)
        return
    vid, page, q = parse_cb_id(parts[2]), parse_cb_id(parts[3]), parse_cb_id(parts[4])
    direction = parts[5]
    if vid is None or page is None or q is None or direction not in ("dec", "inc"):
        await _expired(update)
        return
    variant = products_repo.get_variant(vid)
    if variant is None or not products_repo.is_variant_available(variant):
        await show_variant_screen(update, context, vid, page, q)  # shows the unavailable alert
        return
    limit = cart.max_quantity_for(variant["delivery_kind"], variant["key_stock"])
    new_q = q - 1 if direction == "dec" else q + 1
    clamped = max(MIN_QTY, min(limit, new_q))
    if clamped == q:
        alert = (
            t("catalogue.min_qty_alert", min=MIN_QTY)
            if direction == "dec"
            else t("catalogue.max_qty_alert", max=limit)
        )
        await query.answer(alert, show_alert=True)
        return
    await show_variant_screen(update, context, vid, page, clamped)


async def cat_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = require_query(update)
    user_id = require_user(update).id
    parts = parse_callback(require_data(query), 5)
    if parts is None:
        await _expired(update)
        return
    vid, page, q = parse_cb_id(parts[2]), parse_cb_id(parts[3]), parse_cb_id(parts[4])
    if vid is None or page is None or q is None:
        await _expired(update)
        return
    touch_customer(update)

    variant = products_repo.get_variant(vid)
    if variant is None or not products_repo.is_variant_available(variant):
        await query.answer(unavailable_alert(variant), show_alert=True)
        return

    existing = cart.current_qty(user_id, vid)
    limit = cart.max_quantity_for(variant["delivery_kind"], variant["key_stock"])
    if limit <= 0:
        await query.answer(t("catalogue.out_of_stock_alert"), show_alert=True)
        return
    if existing >= limit:
        key = (
            "catalogue.max_qty_alert"
            if _is_stepper_edition(variant)
            else "catalogue.already_in_cart_alert"
        )
        await query.answer(t(key, max=limit), show_alert=True)
        return

    effective = cart.add(user_id, vid, q)
    added = effective - existing
    if added <= 0:  # stock vanished between the check above and the write
        await query.answer(t("catalogue.out_of_stock_alert"), show_alert=True)
        return

    body = t(
        "catalogue.added_to_cart",
        qty=added,
        product=esc(trim(variant["product_name"], NAME_BUDGET)),
        variant=esc(trim(variant["variant_label"], NAME_BUDGET)),
    )
    if added < q:
        body += "\n" + esc(t("catalogue.added_capped_note"))
    text = screen(ICON.ok, t("catalogue.added_title"), body)

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t("catalogue.view_cart_button"), callback_data=cb("cart", "view")
                )
            ],
            [
                InlineKeyboardButton(
                    t("catalogue.keep_browsing_button"),
                    callback_data=cb("cat", "cat", variant["category_id"], page),
                )
            ],
            nav_row(),
        ]
    )
    await show_screen(update, context, text=text, keyboard=keyboard)


async def route(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = require_query(update)
    parts = parse_callback(require_data(query), range(2, 7))
    if parts is None:
        await _expired(update)
        return
    action = parts[1]
    if action == "cats":
        await show_categories(update, context)
    elif action == "cat":
        await cat_cat(update, context)
    elif action == "p":
        await cat_p(update, context)
    elif action == "v":
        await cat_v(update, context)
    elif action == "q":
        await cat_q(update, context)
    elif action == "add":
        await cat_add(update, context)
    else:
        await _expired(update)
