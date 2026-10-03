"""Catalogue browsing: category list -> product list -> product card ->
variant screen -> add to cart. Registered under the "cat:" callback namespace
and routed by action word."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from ..i18n import t
from ..keyboards import cb, parse_callback, parse_cb_id
from ..render import bold, esc, usd
from ..repo import cart, categories
from ..repo import products as products_repo
from ..ui import show_screen
from .common import get_lang, touch_customer

PAGE_SIZE = 5
MIN_QTY = 1
MAX_QTY = 99


def _cart_label(user_id: int, lang: str) -> str:
    item_count, _ = cart.summary(user_id)
    return (
        t("common.my_cart_count", lang, count=item_count)
        if item_count
        else t("common.my_cart_button", lang)
    )


# ---------------------------------------------------------------------------
# Category list
# ---------------------------------------------------------------------------


async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    touch_customer(update)
    user_id = update.effective_user.id
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
        await show_screen(update, context, text=t("catalogue.no_stock", lang), keyboard=keyboard)
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

    text = (
        f"{bold(t('catalogue.our_teas_heading', lang))}\n\n{t('catalogue.choose_category', lang)}"
    )
    await show_screen(update, context, text=text, keyboard=InlineKeyboardMarkup(buttons))


# ---------------------------------------------------------------------------
# Product list (within a category)
# ---------------------------------------------------------------------------


async def show_products(
    update: Update, context: ContextTypes.DEFAULT_TYPE, category_id: int, page: int
) -> None:
    touch_customer(update)
    user_id = update.effective_user.id
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
            t("catalogue.price_from", lang, price=usd(p["min_price_usd"]))
            if p["min_price_usd"] is not None
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
# Product card (variant buttons)
# ---------------------------------------------------------------------------


async def show_product_card(
    update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int, page: int
) -> None:
    touch_customer(update)
    user_id = update.effective_user.id
    lang = get_lang(user_id)

    product = products_repo.get(product_id)
    if product is None:
        await show_categories(update, context)
        return

    variants = products_repo.list_variants(product_id)

    lines = [bold(product["name"])]
    if product["description"]:
        lines.append(esc(product["description"]))
    if not variants:
        lines.append(t("catalogue.no_weights", lang))
    text = "\n\n".join(lines)

    buttons = [
        [
            InlineKeyboardButton(
                f"{v['label']} — {usd(v['price_usd'])}", callback_data=cb("cat", "v", v["id"], page)
            )
        ]
        for v in variants
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

    await show_screen(update, context, text=text, keyboard=InlineKeyboardMarkup(buttons))


# ---------------------------------------------------------------------------
# Variant screen (quantity stepper)
# ---------------------------------------------------------------------------


async def show_variant_screen(
    update: Update, context: ContextTypes.DEFAULT_TYPE, variant_id: int, page: int, qty: int
) -> None:
    touch_customer(update)
    user_id = update.effective_user.id
    lang = get_lang(user_id)

    variant = products_repo.get_variant(variant_id)
    if variant is None:
        await show_categories(update, context)
        return

    line_total = variant["price_usd"] * qty
    lines = [
        bold(variant["product_name"]),
        t("catalogue.weight_label", lang, label=esc(variant["variant_label"])),
        t("catalogue.unit_price_label", lang, price=usd(variant["price_usd"])),
        "",
        bold(t("catalogue.total_for_qty", lang, qty=qty, total=usd(line_total))),
    ]
    text = "\n".join(lines)

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "−", callback_data=cb("cat", "q", variant_id, page, qty, "dec")
                ),
                InlineKeyboardButton(str(qty), callback_data="noop"),
                InlineKeyboardButton(
                    "+", callback_data=cb("cat", "q", variant_id, page, qty, "inc")
                ),
            ],
            [
                InlineKeyboardButton(
                    t("catalogue.add_to_cart_button", lang),
                    callback_data=cb("cat", "add", variant_id, page, qty),
                )
            ],
            [
                InlineKeyboardButton(
                    t("catalogue.back_to_product", lang),
                    callback_data=cb("cat", "p", variant["product_id"], page),
                ),
                InlineKeyboardButton(_cart_label(user_id, lang), callback_data=cb("cart", "view")),
            ],
        ]
    )
    await show_screen(update, context, text=text, keyboard=keyboard)


# ---------------------------------------------------------------------------
# Callback handlers
# ---------------------------------------------------------------------------


async def cat_cat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = get_lang(update.effective_user.id)
    parts = parse_callback(update.callback_query.data, 4)
    category_id = parse_cb_id(parts[2]) if parts else None
    page = parse_cb_id(parts[3]) if parts else None
    if category_id is None or page is None:
        await update.callback_query.answer(t("common.expired_menu", lang), show_alert=True)
        return
    await show_products(update, context, category_id, page)


async def cat_p(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = get_lang(update.effective_user.id)
    parts = parse_callback(update.callback_query.data, 4)
    product_id = parse_cb_id(parts[2]) if parts else None
    page = parse_cb_id(parts[3]) if parts else None
    if product_id is None or page is None:
        await update.callback_query.answer(t("common.expired_menu", lang), show_alert=True)
        return
    await show_product_card(update, context, product_id, page)


async def cat_v(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = get_lang(update.effective_user.id)
    parts = parse_callback(update.callback_query.data, 4)
    variant_id = parse_cb_id(parts[2]) if parts else None
    page = parse_cb_id(parts[3]) if parts else None
    if variant_id is None or page is None:
        await update.callback_query.answer(t("common.expired_menu", lang), show_alert=True)
        return
    await show_variant_screen(update, context, variant_id, page, qty=MIN_QTY)


async def cat_q(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = get_lang(update.effective_user.id)
    parts = parse_callback(update.callback_query.data, 6)
    if parts is None:
        await update.callback_query.answer(t("common.expired_menu", lang), show_alert=True)
        return
    vid, page, q = parse_cb_id(parts[2]), parse_cb_id(parts[3]), parse_cb_id(parts[4])
    direction = parts[5]
    if vid is None or page is None or q is None or direction not in ("dec", "inc"):
        await update.callback_query.answer(t("common.expired_menu", lang), show_alert=True)
        return
    new_q = q - 1 if direction == "dec" else q + 1
    clamped = max(MIN_QTY, min(MAX_QTY, new_q))
    if clamped == q:
        alert = (
            t("catalogue.min_qty_alert", lang, min=MIN_QTY)
            if direction == "dec"
            else t("catalogue.max_qty_alert", lang, max=MAX_QTY)
        )
        await update.callback_query.answer(alert, show_alert=True)
        return
    await show_variant_screen(update, context, vid, page, clamped)


async def cat_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = get_lang(update.effective_user.id)
    parts = parse_callback(update.callback_query.data, 5)
    if parts is None:
        await update.callback_query.answer(t("common.expired_menu", lang), show_alert=True)
        return
    vid, page, q = parse_cb_id(parts[2]), parse_cb_id(parts[3]), parse_cb_id(parts[4])
    if vid is None or page is None or q is None:
        await update.callback_query.answer(t("common.expired_menu", lang), show_alert=True)
        return
    touch_customer(update)

    variant = products_repo.get_variant(vid)
    if variant is None:
        await update.callback_query.answer(
            t("catalogue.item_unavailable_alert", lang), show_alert=True
        )
        return

    user_id = update.effective_user.id
    cart.add(user_id, vid, q)

    product_name = bold(variant["product_name"])
    variant_label = esc(variant["variant_label"])
    text = t("catalogue.added_to_cart", lang, qty=q, product=product_name, variant=variant_label)

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
    query = update.callback_query
    lang = get_lang(update.effective_user.id) if update.effective_user else "en"
    parts = parse_callback(query.data, range(2, 7))
    if parts is None:
        await query.answer(t("common.expired_menu", lang), show_alert=True)
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
        await query.answer(t("common.expired_menu", lang), show_alert=True)
