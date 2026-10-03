"""Catalogue browsing: category list -> product list -> product card (editions)
-> edition screen -> add to cart. Registered under the "cat:" callback
namespace and routed by action word."""

from __future__ import annotations

import sqlite3

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from ..i18n import t
from ..keyboards import cb, parse_callback, parse_cb_id
from ..render import bold, esc, stars
from ..repo import cart, categories
from ..repo import products as products_repo
from ..tg import require_data, require_query, require_user
from ..ui import show_screen
from .common import get_lang, touch_customer

PAGE_SIZE = 5
MIN_QTY = 1


def delivery_label(delivery_kind: str, lang: str) -> str:
    return t(f"delivery.{delivery_kind}", lang)


def _cart_label(user_id: int, lang: str) -> str:
    item_count, _ = cart.summary(user_id)
    return (
        t("common.my_cart_count", lang, count=item_count)
        if item_count
        else t("common.my_cart_button", lang)
    )


def unavailable_alert(variant: sqlite3.Row | None, lang: str) -> str:
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
        return t("catalogue.out_of_stock_alert", lang)
    return t("catalogue.item_unavailable_alert", lang)


def _is_stepper_edition(variant: sqlite3.Row) -> bool:
    """Only key editions have a quantity stepper; file and text editions are
    one per customer."""
    return bool(variant["delivery_kind"] == "key")


# ---------------------------------------------------------------------------
# Category list
# ---------------------------------------------------------------------------


async def show_categories(
    update: Update, context: ContextTypes.DEFAULT_TYPE, alert: str | None = None
) -> None:
    touch_customer(update)
    user_id = require_user(update).id
    lang = get_lang(user_id)

    cats = categories.list_active()
    if not cats:
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        t("common.back_to_menu_button", lang), callback_data=cb("menu", "show")
                    )
                ]
            ]
        )
        await show_screen(
            update, context, text=t("catalogue.no_stock", lang), keyboard=keyboard, alert=alert
        )
        return

    buttons = [
        [InlineKeyboardButton(c["name"], callback_data=cb("cat", "cat", c["id"], 0))] for c in cats
    ]
    buttons.append(
        [
            InlineKeyboardButton(_cart_label(user_id, lang), callback_data=cb("cart", "view")),
            InlineKeyboardButton(
                t("common.back_to_menu_button", lang), callback_data=cb("menu", "show")
            ),
        ]
    )

    text = f"{bold(t('catalogue.heading', lang))}\n\n{t('catalogue.choose_category', lang)}"
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
    lang = get_lang(user_id)

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
            t("catalogue.price_from", lang, price=stars(p["min_price_stars"]))
            if p["min_price_stars"] is not None
            else ""
        )
        label = f"{p['name']}{price_hint}"
        buttons.append([InlineKeyboardButton(label, callback_data=cb("cat", "p", p["id"], page))])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀", callback_data=cb("cat", "cat", category_id, page - 1)))
    nav.append(InlineKeyboardButton(f"Page {page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("▶", callback_data=cb("cat", "cat", category_id, page + 1)))
    buttons.append(nav)

    buttons.append(
        [
            InlineKeyboardButton(_cart_label(user_id, lang), callback_data=cb("cart", "view")),
            InlineKeyboardButton(
                t("catalogue.back_to_categories", lang), callback_data=cb("cat", "cats")
            ),
        ]
    )

    text = f"{bold(category['name'])}\n\n{t('catalogue.tap_to_view', lang)}"
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
    lang = get_lang(user_id)

    product = products_repo.get(product_id)
    if product is None:
        await show_categories(update, context, alert=alert)
        return

    editions = products_repo.list_variants(product_id)

    lines = [bold(product["name"])]
    if product["description"]:
        lines.append(esc(product["description"]))
    if not editions:
        lines.append(t("catalogue.no_editions", lang))
    text = "\n\n".join(lines)

    buttons = [
        [
            InlineKeyboardButton(
                f"{v['label']} — {stars(v['price_stars'])}",
                callback_data=cb("cat", "v", v["id"], page),
            )
        ]
        for v in editions
    ]
    buttons.append(
        [
            InlineKeyboardButton(
                t("catalogue.back_to_products", lang),
                callback_data=cb("cat", "cat", product["category_id"], page),
            ),
            InlineKeyboardButton(_cart_label(user_id, lang), callback_data=cb("cart", "view")),
        ]
    )

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
    lang = get_lang(user_id)

    variant = products_repo.get_variant(variant_id)
    if variant is None or not products_repo.is_variant_available(variant):
        alert = unavailable_alert(variant, lang)
        if variant is not None and variant["product_is_active"]:
            await show_product_card(update, context, variant["product_id"], page, alert=alert)
        else:
            await show_categories(update, context, alert=alert)
        return

    lines = [
        bold(variant["product_name"]),
        t("catalogue.edition_label", lang, label=esc(variant["variant_label"])),
        t("catalogue.price_label", lang, price=stars(variant["price_stars"])),
        t(
            "catalogue.delivery_line",
            lang,
            kind=delivery_label(variant["delivery_kind"], lang),
        ),
    ]
    rows: list[list[InlineKeyboardButton]] = []
    stepper = _is_stepper_edition(variant)
    add_qty = MIN_QTY
    if stepper:
        limit = cart.max_quantity_for(variant["delivery_kind"], variant["key_stock"])
        qty = max(MIN_QTY, min(qty, limit))
        add_qty = qty
        lines += [
            "",
            bold(
                t(
                    "catalogue.total_for_qty",
                    lang,
                    qty=qty,
                    total=stars(variant["price_stars"] * qty),
                )
            ),
        ]
        rows.append(
            [
                InlineKeyboardButton(
                    "−", callback_data=cb("cat", "q", variant_id, page, qty, "dec")
                ),
                InlineKeyboardButton(str(qty), callback_data="noop"),
                InlineKeyboardButton(
                    "+", callback_data=cb("cat", "q", variant_id, page, qty, "inc")
                ),
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                t("catalogue.add_to_cart_button", lang),
                callback_data=cb("cat", "add", variant_id, page, add_qty),
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                t("catalogue.back_to_product", lang),
                callback_data=cb("cat", "p", variant["product_id"], page),
            ),
            InlineKeyboardButton(_cart_label(user_id, lang), callback_data=cb("cart", "view")),
        ]
    )
    await show_screen(update, context, text="\n".join(lines), keyboard=InlineKeyboardMarkup(rows))


# ---------------------------------------------------------------------------
# Callback handlers
# ---------------------------------------------------------------------------


async def _expired(update: Update) -> None:
    lang = get_lang(require_user(update).id)
    await require_query(update).answer(t("common.expired_menu", lang), show_alert=True)


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
    lang = get_lang(require_user(update).id)
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
            t("catalogue.min_qty_alert", lang, min=MIN_QTY)
            if direction == "dec"
            else t("catalogue.max_qty_alert", lang, max=limit)
        )
        await query.answer(alert, show_alert=True)
        return
    await show_variant_screen(update, context, vid, page, clamped)


async def cat_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = require_query(update)
    user_id = require_user(update).id
    lang = get_lang(user_id)
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
        await query.answer(unavailable_alert(variant, lang), show_alert=True)
        return

    existing = cart.current_qty(user_id, vid)
    limit = cart.max_quantity_for(variant["delivery_kind"], variant["key_stock"])
    if limit <= 0:
        await query.answer(t("catalogue.out_of_stock_alert", lang), show_alert=True)
        return
    if existing >= limit:
        key = (
            "catalogue.max_qty_alert"
            if _is_stepper_edition(variant)
            else "catalogue.already_in_cart_alert"
        )
        await query.answer(t(key, lang, max=limit), show_alert=True)
        return

    effective = cart.add(user_id, vid, q)
    added = effective - existing
    if added <= 0:  # stock vanished between the check above and the write
        await query.answer(t("catalogue.out_of_stock_alert", lang), show_alert=True)
        return

    text = t(
        "catalogue.added_to_cart",
        lang,
        qty=added,
        product=bold(variant["product_name"]),
        variant=esc(variant["variant_label"]),
    )
    if added < q:
        text += "\n" + t("catalogue.added_capped_note", lang)

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t("catalogue.keep_browsing_button", lang),
                    callback_data=cb("cat", "cat", variant["category_id"], page),
                )
            ],
            [
                InlineKeyboardButton(
                    t("catalogue.view_cart_button", lang), callback_data=cb("cart", "view")
                )
            ],
            [
                InlineKeyboardButton(
                    t("common.back_to_menu_button", lang), callback_data=cb("menu", "show")
                )
            ],
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
