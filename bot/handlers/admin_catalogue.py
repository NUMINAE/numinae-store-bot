"""The admin catalogue manager: dashboard, and category/product/edition
CRUD with typed-input prompts backed by admin_pending_input (not
context.user_data, so an edit survives a bot restart). Registered under the
"adm:" callback namespace; every handler here is owner-only.
"""

from __future__ import annotations

import sqlite3

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from ..keyboards import cb, parse_callback, parse_cb_id
from ..render import bold, esc, stars
from ..repo import admin_pending as pending_repo
from ..repo import categories as categories_repo
from ..repo import license_keys as keys_repo
from ..repo import products as products_repo
from ..repo import variants as variants_repo
from ..security import owner_only
from ..tg import require_data, require_message, require_query, require_text, require_user
from ..ui import show_screen
from . import admin_common, admin_settings
from .admin_common import Invalid

PRODUCTS_PAGE_SIZE = 5

_DELIVERY_NAMES = {"file": "File", "key": "Keys", "text": "Text"}
_KIND_BY_CODE = {"f": "file", "k": "key", "t": "text"}
_PRICE_PROMPT = "Send the price in whole Stars, e.g. 75 or ⭐75 (1–100000)."
_DELIVERY_TEXT_PROMPT = (
    "Send the text customers receive after paying — e.g. a download link or instructions "
    "(1–2000 characters)."
)

_EXPIRED_MENU_ALERT = "This menu has expired."

# Thin aliases onto the shared helpers in admin_common — kept under these
# names so the rest of this file (written before the extraction) doesn't
# need touching call-by-call.
_validate_name = admin_common.validate_name
_validate_description = admin_common.validate_description
_validate_price = admin_common.validate_price
_validate_sort_order = admin_common.validate_sort_order
_cancel_button = admin_common.cancel_button
_prompt = admin_common.prompt
_reprompt_after_invalid = admin_common.reprompt_after_invalid
_status_word = admin_common.status_word
_toggle_label = admin_common.toggle_label


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@owner_only
async def dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cat_count = categories_repo.count_all()
    prod_count = products_repo.count_all()

    lines = [
        bold("Admin dashboard"),
        "",
        f"Catalogue — {cat_count} categor{'y' if cat_count == 1 else 'ies'}, "
        f"{prod_count} product{'' if prod_count == 1 else 's'}",
    ]
    text = "\n".join(lines)

    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Catalogue", callback_data=cb("adm", "cat"))],
            [InlineKeyboardButton("Orders", callback_data=cb("adm", "o"))],
            [InlineKeyboardButton("Settings", callback_data=cb("adm", "set"))],
        ]
    )
    await show_screen(update, context, text=text, keyboard=keyboard)


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------


async def show_category_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cats = categories_repo.list_all()
    buttons = []
    for c in cats:
        product_count = categories_repo.count_products(c["id"])
        label = f"{c['name']} ({product_count}) · {_status_word(c['is_active'])}"
        buttons.append([InlineKeyboardButton(label, callback_data=cb("adm", "cat", "v", c["id"]))])
    buttons.append([InlineKeyboardButton("Add category", callback_data=cb("adm", "cat", "add"))])
    buttons.append([InlineKeyboardButton("Back to admin", callback_data=cb("adm", "home"))])

    text = bold("Categories") if cats else f"{bold('Categories')}\n\nNo categories yet."
    await show_screen(update, context, text=text, keyboard=InlineKeyboardMarkup(buttons))


async def show_category_detail(
    update: Update, context: ContextTypes.DEFAULT_TYPE, category_id: int
) -> None:
    category = categories_repo.get(category_id)
    if category is None:
        await show_category_list(update, context)
        return

    product_count = categories_repo.count_products(category_id)
    lines = [bold(category["name"])]
    lines.append(esc(category["description"]) if category["description"] else "(no description)")
    lines += [
        "",
        f"Sort order: {category['sort_order']}",
        f"Status: {_status_word(category['is_active'])}",
        f"Products: {product_count}",
    ]
    text = "\n".join(lines)

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Edit name", callback_data=cb("adm", "cat", "en", category_id)
                ),
                InlineKeyboardButton(
                    "Edit description", callback_data=cb("adm", "cat", "ed", category_id)
                ),
            ],
            [
                InlineKeyboardButton(
                    "Sort order", callback_data=cb("adm", "cat", "so", category_id)
                ),
                InlineKeyboardButton(
                    _toggle_label(category["is_active"]),
                    callback_data=cb("adm", "cat", "tg", category_id),
                ),
            ],
            [
                InlineKeyboardButton(
                    f"Manage products ({product_count})",
                    callback_data=cb("adm", "pr", category_id, 0),
                )
            ],
            [InlineKeyboardButton("Back to categories", callback_data=cb("adm", "cat"))],
        ]
    )
    await show_screen(update, context, text=text, keyboard=keyboard)


async def cat_add_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pending_repo.set(require_user(update).id, "cat_create_name")
    await _prompt(update, context, "What's the name of the new category? (1–60 characters)")


async def cat_edit_name_prompt(
    update: Update, context: ContextTypes.DEFAULT_TYPE, category_id: int
) -> None:
    pending_repo.set(require_user(update).id, "cat_edit_name", target_id=category_id)
    await _prompt(update, context, "Send the new name for this category (1–60 characters).")


async def cat_edit_desc_prompt(
    update: Update, context: ContextTypes.DEFAULT_TYPE, category_id: int
) -> None:
    pending_repo.set(require_user(update).id, "cat_edit_desc", target_id=category_id)
    await _prompt(
        update,
        context,
        'Send the new description (0–500 characters). Send a single "-" to clear it.',
    )


async def cat_edit_sort_prompt(
    update: Update, context: ContextTypes.DEFAULT_TYPE, category_id: int
) -> None:
    pending_repo.set(require_user(update).id, "cat_edit_sort", target_id=category_id)
    await _prompt(update, context, "Send the new sort order — a whole number between 0 and 999.")


async def cat_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE, category_id: int) -> None:
    category = categories_repo.get(category_id)
    if category is None:
        await show_category_list(update, context)
        return

    if not category["is_active"]:
        categories_repo.set_active(category_id, True)
        await show_category_detail(update, context, category_id)
        return

    product_count = categories_repo.count_products(category_id)
    variant_count = categories_repo.count_purchasable_variants(category_id)
    text = (
        f'Deactivate "{esc(category["name"])}"? This hides {product_count} '
        f"product{'' if product_count == 1 else 's'} and {variant_count} "
        f"edition{'' if variant_count == 1 else 's'} from customers."
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Confirm", callback_data=cb("adm", "cat", "tgc", category_id)),
                InlineKeyboardButton("Cancel", callback_data=cb("adm", "cat", "v", category_id)),
            ]
        ]
    )
    await show_screen(update, context, text=text, keyboard=keyboard)


async def cat_toggle_confirm(
    update: Update, context: ContextTypes.DEFAULT_TYPE, category_id: int
) -> None:
    categories_repo.set_active(category_id, False)
    await show_category_detail(update, context, category_id)


async def route_category(
    update: Update, context: ContextTypes.DEFAULT_TYPE, parts: list[str]
) -> None:
    query = require_query(update)
    # "adm:cat" (list) | "adm:cat:add" | "adm:cat:{action}:{id}" | "adm:cat:v:{id}"
    if len(parts) == 2:
        await show_category_list(update, context)
        return
    action = parts[2]
    if action == "add":
        await cat_add_prompt(update, context)
        return
    if len(parts) < 4:
        await query.answer(_EXPIRED_MENU_ALERT, show_alert=True)
        return
    category_id = parse_cb_id(parts[3])
    if category_id is None:
        await query.answer(_EXPIRED_MENU_ALERT, show_alert=True)
        return
    if action == "v":
        await show_category_detail(update, context, category_id)
    elif action == "en":
        await cat_edit_name_prompt(update, context, category_id)
    elif action == "ed":
        await cat_edit_desc_prompt(update, context, category_id)
    elif action == "so":
        await cat_edit_sort_prompt(update, context, category_id)
    elif action == "tg":
        await cat_toggle(update, context, category_id)
    elif action == "tgc":
        await cat_toggle_confirm(update, context, category_id)
    else:
        await query.answer("This menu has expired.", show_alert=True)


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------


async def show_product_list(
    update: Update, context: ContextTypes.DEFAULT_TYPE, category_id: int, page: int
) -> None:
    category = categories_repo.get(category_id)
    if category is None:
        await show_category_list(update, context)
        return

    total = products_repo.count_all_in_category(category_id)
    buttons = []
    if total > 0:
        total_pages = (total + PRODUCTS_PAGE_SIZE - 1) // PRODUCTS_PAGE_SIZE
        page = max(0, min(page, total_pages - 1))
        rows = products_repo.list_all_in_category(
            category_id, PRODUCTS_PAGE_SIZE, page * PRODUCTS_PAGE_SIZE
        )
        for p in rows:
            variant_count = products_repo.count_variants(p["id"])
            label = f"{p['name']} ({variant_count}) · {_status_word(p['is_active'])}"
            buttons.append(
                [InlineKeyboardButton(label, callback_data=cb("adm", "pr", "v", p["id"]))]
            )

        nav = []
        if page > 0:
            nav.append(
                InlineKeyboardButton("◀", callback_data=cb("adm", "pr", category_id, page - 1))
            )
        nav.append(InlineKeyboardButton(f"Page {page + 1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            nav.append(
                InlineKeyboardButton("▶", callback_data=cb("adm", "pr", category_id, page + 1))
            )
        buttons.append(nav)

    buttons.append(
        [InlineKeyboardButton("Add product", callback_data=cb("adm", "pr", "add", category_id))]
    )
    buttons.append(
        [InlineKeyboardButton("Back to category", callback_data=cb("adm", "cat", "v", category_id))]
    )

    heading = bold(f"Products in {category['name']}")
    text = heading if total else f"{heading}\n\nNo products yet."
    await show_screen(update, context, text=text, keyboard=InlineKeyboardMarkup(buttons))


async def show_product_detail(
    update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int
) -> None:
    product = products_repo.get(product_id)
    if product is None:
        await show_category_list(update, context)
        return
    category = categories_repo.get(product["category_id"])
    variants = variants_repo.list_all_for_product(product_id)

    lines = [bold(product["name"])]
    lines.append(esc(product["description"]) if product["description"] else "(no description)")
    lines += [
        "",
        f"Category: {esc(category['name']) if category else '(unknown)'}",
        f"Sort order: {product['sort_order']}",
        f"Status: {_status_word(product['is_active'])}",
    ]

    if variants:
        lines += ["", bold("Editions:")]
        for v in variants:
            lines.append(
                f"{esc(v['label'])} — {stars(v['price_stars'])} · "
                f"{_DELIVERY_NAMES[v['delivery_kind']]} · {_status_word(v['is_active'])}"
            )
    else:
        lines += ["", "This product has no editions yet — add one?"]
    text = "\n".join(lines)

    buttons = [
        [
            InlineKeyboardButton("Edit name", callback_data=cb("adm", "pr", "en", product_id)),
            InlineKeyboardButton(
                "Edit description", callback_data=cb("adm", "pr", "ed", product_id)
            ),
        ],
        [
            InlineKeyboardButton(
                "Move to category", callback_data=cb("adm", "pr", "mv", product_id)
            ),
            InlineKeyboardButton("Sort order", callback_data=cb("adm", "pr", "so", product_id)),
        ],
        [
            InlineKeyboardButton(
                _toggle_label(product["is_active"]), callback_data=cb("adm", "pr", "tg", product_id)
            )
        ],
    ]
    if not variants:
        buttons.append(
            [InlineKeyboardButton("Add edition", callback_data=cb("adm", "va", "add", product_id))]
        )
    buttons.append(
        [
            InlineKeyboardButton(
                f"Manage editions ({len(variants)})", callback_data=cb("adm", "va", product_id)
            )
        ]
    )
    buttons.append(
        [
            InlineKeyboardButton(
                "Back to products", callback_data=cb("adm", "pr", product["category_id"], 0)
            )
        ]
    )

    await show_screen(update, context, text=text, keyboard=InlineKeyboardMarkup(buttons))


async def prod_add_prompt(
    update: Update, context: ContextTypes.DEFAULT_TYPE, category_id: int
) -> None:
    pending_repo.set(require_user(update).id, "prod_create_name", context_id=category_id)
    await _prompt(update, context, "What's the name of the new product? (1–60 characters)")


async def prod_edit_name_prompt(
    update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int
) -> None:
    pending_repo.set(require_user(update).id, "prod_edit_name", target_id=product_id)
    await _prompt(update, context, "Send the new name for this product (1–60 characters).")


async def prod_edit_desc_prompt(
    update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int
) -> None:
    pending_repo.set(require_user(update).id, "prod_edit_desc", target_id=product_id)
    await _prompt(
        update,
        context,
        'Send the new description (0–500 characters). Send a single "-" to clear it.',
    )


async def prod_edit_sort_prompt(
    update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int
) -> None:
    pending_repo.set(require_user(update).id, "prod_edit_sort", target_id=product_id)
    await _prompt(update, context, "Send the new sort order — a whole number between 0 and 999.")


async def prod_move_picker(
    update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int
) -> None:
    product = products_repo.get(product_id)
    if product is None:
        await show_category_list(update, context)
        return
    cats = categories_repo.list_all()
    buttons = [
        [
            InlineKeyboardButton(
                f"{c['name']}{' (current)' if c['id'] == product['category_id'] else ''}",
                callback_data=cb("adm", "pr", "mvto", product_id, c["id"]),
            )
        ]
        for c in cats
    ]
    buttons.append([InlineKeyboardButton("Cancel", callback_data=cb("adm", "pr", "v", product_id))])
    text = f"Move {bold(product['name'])} to which category?"
    await show_screen(update, context, text=text, keyboard=InlineKeyboardMarkup(buttons))


async def prod_move_apply(
    update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int, category_id: int
) -> None:
    products_repo.update_category(product_id, category_id)
    await show_product_detail(update, context, product_id)


async def prod_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int) -> None:
    product = products_repo.get(product_id)
    if product is None:
        await show_category_list(update, context)
        return

    if not product["is_active"]:
        products_repo.set_active(product_id, True)
        await show_product_detail(update, context, product_id)
        return

    variant_count = products_repo.count_purchasable_variants(product_id)
    text = (
        f'Deactivate "{esc(product["name"])}"? This hides {variant_count} '
        f"edition{'' if variant_count == 1 else 's'} from customers."
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Confirm", callback_data=cb("adm", "pr", "tgc", product_id)),
                InlineKeyboardButton("Cancel", callback_data=cb("adm", "pr", "v", product_id)),
            ]
        ]
    )
    await show_screen(update, context, text=text, keyboard=keyboard)


async def prod_toggle_confirm(
    update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int
) -> None:
    products_repo.set_active(product_id, False)
    await show_product_detail(update, context, product_id)


async def route_product(
    update: Update, context: ContextTypes.DEFAULT_TYPE, parts: list[str]
) -> None:
    query = require_query(update)
    # "adm:pr:{category_id}:{page}" | "adm:pr:add:{category_id}" | "adm:pr:{action}:{id}" |
    # "adm:pr:v:{id}" | "adm:pr:mvto:{id}:{category_id}"
    if len(parts) < 4:
        await query.answer(_EXPIRED_MENU_ALERT, show_alert=True)
        return

    action_or_id = parts[2]
    maybe_category_id = parse_cb_id(action_or_id)
    if maybe_category_id is not None:
        page = parse_cb_id(parts[3])
        if page is None:
            await query.answer(_EXPIRED_MENU_ALERT, show_alert=True)
            return
        await show_product_list(update, context, maybe_category_id, page)
        return

    action = action_or_id
    if action == "add":
        category_id = parse_cb_id(parts[3])
        if category_id is None:
            await query.answer(_EXPIRED_MENU_ALERT, show_alert=True)
            return
        await prod_add_prompt(update, context, category_id)
        return

    product_id = parse_cb_id(parts[3])
    if product_id is None:
        await query.answer(_EXPIRED_MENU_ALERT, show_alert=True)
        return
    if action == "v":
        await show_product_detail(update, context, product_id)
    elif action == "en":
        await prod_edit_name_prompt(update, context, product_id)
    elif action == "ed":
        await prod_edit_desc_prompt(update, context, product_id)
    elif action == "so":
        await prod_edit_sort_prompt(update, context, product_id)
    elif action == "mv":
        await prod_move_picker(update, context, product_id)
    elif action == "mvto":
        if len(parts) < 5:
            await query.answer(_EXPIRED_MENU_ALERT, show_alert=True)
            return
        target_category_id = parse_cb_id(parts[4])
        if target_category_id is None:
            await query.answer(_EXPIRED_MENU_ALERT, show_alert=True)
            return
        await prod_move_apply(update, context, product_id, target_category_id)
    elif action == "tg":
        await prod_toggle(update, context, product_id)
    elif action == "tgc":
        await prod_toggle_confirm(update, context, product_id)
    else:
        await query.answer(_EXPIRED_MENU_ALERT, show_alert=True)


# ---------------------------------------------------------------------------
# Variants
# ---------------------------------------------------------------------------


async def show_variant_list(
    update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int
) -> None:
    product = products_repo.get(product_id)
    if product is None:
        await show_category_list(update, context)
        return
    variants = variants_repo.list_all_for_product(product_id)

    buttons = []
    for v in variants:
        label = f"{v['label']} — {stars(v['price_stars'])} · {_status_word(v['is_active'])}"
        buttons.append([InlineKeyboardButton(label, callback_data=cb("adm", "va", "v", v["id"]))])
    buttons.append(
        [InlineKeyboardButton("Add edition", callback_data=cb("adm", "va", "add", product_id))]
    )
    buttons.append(
        [InlineKeyboardButton("Back to product", callback_data=cb("adm", "pr", "v", product_id))]
    )

    heading = bold(f"Editions — {product['name']}")
    text = heading if variants else f"{heading}\n\nNo editions yet."
    await show_screen(update, context, text=text, keyboard=InlineKeyboardMarkup(buttons))


async def show_variant_detail(
    update: Update, context: ContextTypes.DEFAULT_TYPE, variant_id: int
) -> None:
    variant = variants_repo.get(variant_id)
    if variant is None:
        await show_category_list(update, context)
        return

    lines = [
        bold(variant["label"]),
        f"Price: {stars(variant['price_stars'])}",
        f"Delivery: {_DELIVERY_NAMES[variant['delivery_kind']]}",
    ]
    if variant["delivery_kind"] == "key":
        lines.append(f"Keys in stock: {keys_repo.count_available(variant_id)}")
    lines += [
        f"Sort order: {variant['sort_order']}",
        f"Status: {_status_word(variant['is_active'])}",
    ]
    warning = _delivery_warning(variant)
    if warning:
        lines += ["", f"⚠️ {warning}"]
    text = "\n".join(lines)

    rows = [
        [
            InlineKeyboardButton("Edit label", callback_data=cb("adm", "va", "el", variant_id)),
            InlineKeyboardButton("Edit price", callback_data=cb("adm", "va", "ep", variant_id)),
        ],
        [
            InlineKeyboardButton("Sort order", callback_data=cb("adm", "va", "so", variant_id)),
            InlineKeyboardButton(
                _toggle_label(variant["is_active"]),
                callback_data=cb("adm", "va", "tg", variant_id),
            ),
        ],
    ]
    if variant["delivery_kind"] == "text":
        rows.append(
            [
                InlineKeyboardButton(
                    "Set delivery text", callback_data=cb("adm", "va", "dt", variant_id)
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                "Back to editions", callback_data=cb("adm", "va", variant["product_id"])
            )
        ]
    )
    await show_screen(update, context, text=text, keyboard=InlineKeyboardMarkup(rows))


def _delivery_warning(variant: sqlite3.Row) -> str | None:
    """Why this edition is hidden from customers for lack of delivery content."""
    kind = variant["delivery_kind"]
    if kind == "text" and not (variant["delivery_text"] or "").strip():
        return "Hidden from customers: no delivery content yet (set the delivery text)."
    if kind == "file" and not variant["delivery_file_id"]:
        return "Hidden from customers: no delivery content yet (file upload is not available yet)."
    if kind == "key" and keys_repo.count_available(variant["id"]) == 0:
        return "Hidden from customers: no delivery content yet (no keys in stock)."
    return None


async def var_add_label_prompt(
    update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int
) -> None:
    pending_repo.set(require_user(update).id, "var_create_label", context_id=product_id)
    await _prompt(
        update,
        context,
        'What\'s the label for the new edition? (e.g. "Personal", 1–60 characters)',
    )


async def var_edit_label_prompt(
    update: Update, context: ContextTypes.DEFAULT_TYPE, variant_id: int
) -> None:
    pending_repo.set(require_user(update).id, "var_edit_label", target_id=variant_id)
    await _prompt(update, context, "Send the new label (1–60 characters).")


async def var_edit_price_prompt(
    update: Update, context: ContextTypes.DEFAULT_TYPE, variant_id: int
) -> None:
    pending_repo.set(require_user(update).id, "var_edit_price", target_id=variant_id)
    await _prompt(update, context, _PRICE_PROMPT)


async def var_edit_sort_prompt(
    update: Update, context: ContextTypes.DEFAULT_TYPE, variant_id: int
) -> None:
    pending_repo.set(require_user(update).id, "var_edit_sort", target_id=variant_id)
    await _prompt(update, context, "Send the new sort order — a whole number between 0 and 999.")


async def var_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE, variant_id: int) -> None:
    variant = variants_repo.get(variant_id)
    if variant is None:
        await show_category_list(update, context)
        return

    if not variant["is_active"]:
        variants_repo.set_active(variant_id, True)
        await show_variant_detail(update, context, variant_id)
        return

    product = products_repo.get(variant["product_id"])
    active_count = products_repo.count_active_variants(variant["product_id"])
    product_name = esc(product["name"]) if product else "this product"

    if active_count <= 1:
        text = (
            f'This is the only edition of "{product_name}". Deactivating it will '
            "hide the product from customers."
        )
    else:
        text = f'Deactivate "{esc(variant["label"])}" for "{product_name}"?'

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Confirm", callback_data=cb("adm", "va", "tgc", variant_id)),
                InlineKeyboardButton("Cancel", callback_data=cb("adm", "va", "v", variant_id)),
            ]
        ]
    )
    await show_screen(update, context, text=text, keyboard=keyboard)


async def var_toggle_confirm(
    update: Update, context: ContextTypes.DEFAULT_TYPE, variant_id: int
) -> None:
    variants_repo.set_active(variant_id, False)
    await show_variant_detail(update, context, variant_id)


async def route_variant(
    update: Update, context: ContextTypes.DEFAULT_TYPE, parts: list[str]
) -> None:
    query = require_query(update)
    # "adm:va:{product_id}" | "adm:va:add:{product_id}" | "adm:va:{action}:{id}" | "adm:va:v:{id}"
    if len(parts) < 3:
        await query.answer(_EXPIRED_MENU_ALERT, show_alert=True)
        return

    action_or_id = parts[2]
    maybe_product_id = parse_cb_id(action_or_id)
    if maybe_product_id is not None:
        await show_variant_list(update, context, maybe_product_id)
        return

    if len(parts) < 4:
        await query.answer(_EXPIRED_MENU_ALERT, show_alert=True)
        return

    action = action_or_id
    if action == "ck":
        await var_create_kind(update, context, parts[3])
        return
    if action == "add":
        product_id = parse_cb_id(parts[3])
        if product_id is None:
            await query.answer(_EXPIRED_MENU_ALERT, show_alert=True)
            return
        await var_add_label_prompt(update, context, product_id)
        return

    variant_id = parse_cb_id(parts[3])
    if variant_id is None:
        await query.answer(_EXPIRED_MENU_ALERT, show_alert=True)
        return
    if action == "v":
        await show_variant_detail(update, context, variant_id)
    elif action == "el":
        await var_edit_label_prompt(update, context, variant_id)
    elif action == "ep":
        await var_edit_price_prompt(update, context, variant_id)
    elif action == "dt":
        await var_delivery_text_prompt(update, context, variant_id)
    elif action == "so":
        await var_edit_sort_prompt(update, context, variant_id)
    elif action == "tg":
        await var_toggle(update, context, variant_id)
    elif action == "tgc":
        await var_toggle_confirm(update, context, variant_id)
    else:
        await query.answer("This menu has expired.", show_alert=True)


# ---------------------------------------------------------------------------
# Cancel — reads the pending row (if any), deletes it, and routes back to
# wherever makes sense for the action that was in flight.
# ---------------------------------------------------------------------------


async def cancel_pending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = require_user(update).id
    pending = pending_repo.get(user_id)
    pending_repo.clear(user_id)

    if pending is None:
        await dashboard(update, context)
        return

    action = pending["action"]
    # A pending row is keyed by exactly one of target_id (int) or target_key
    # (str); resolve to whichever is populated rather than picking one field.
    target = pending["target_id"] if pending["target_id"] is not None else pending["target_key"]
    context_id = pending["context_id"]

    if action == "cat_create_name":
        await show_category_list(update, context)
    elif action in ("cat_edit_name", "cat_edit_desc", "cat_edit_sort"):
        await show_category_detail(update, context, target)
    elif action == "prod_create_name":
        await show_product_list(update, context, context_id, 0)
    elif action in ("prod_edit_name", "prod_edit_desc", "prod_edit_sort"):
        await show_product_detail(update, context, target)
    elif action in ("var_create_label", "var_create_price", "var_create_kind"):
        await show_variant_list(update, context, context_id)
    elif action in ("var_edit_label", "var_edit_price", "var_edit_sort", "var_delivery_text"):
        await show_variant_detail(update, context, target)
    elif action == "set_value":
        await admin_settings.show_setting_detail(update, context, target)
    else:
        await dashboard(update, context)


# ---------------------------------------------------------------------------
# Typed-input handling — called from handlers/router.py when the owner has a
# pending admin input and sends a plain-text message.
# ---------------------------------------------------------------------------


async def handle_pending_input(update: Update, context: ContextTypes.DEFAULT_TYPE, pending) -> None:
    action = pending["action"]
    # Same resolution as cancel_pending(): whichever of target_id/target_key
    # is populated is the one this action's handler expects. Handler function
    # signatures are unchanged — they just receive an int or a str depending
    # on the action, exactly as before the target_id/target_key split.
    target = pending["target_id"] if pending["target_id"] is not None else pending["target_key"]
    context_id = pending["context_id"]
    user_id = require_user(update).id
    raw = require_text(require_message(update))

    handler = _PENDING_HANDLERS.get(action)
    if handler is None:
        pending_repo.clear(user_id)
        await dashboard(update, context)
        return

    await handler(update, context, target, context_id, raw)


async def _apply_cat_create_name(update, context, target_id, context_id, raw) -> None:
    result = _validate_name(raw)
    if isinstance(result, Invalid):
        await _reprompt_after_invalid(update, context, result.message)
        return
    value = result.value
    user_id = require_user(update).id
    new_id = categories_repo.create(value)
    pending_repo.clear(user_id)
    await require_message(update).reply_text(
        f'Category "{esc(value)}" created. You can edit its description and sort order from here.'
    )
    await show_category_detail(update, context, new_id)


async def _apply_cat_edit_name(update, context, target_id, context_id, raw) -> None:
    result = _validate_name(raw)
    if isinstance(result, Invalid):
        await _reprompt_after_invalid(update, context, result.message)
        return
    value = result.value
    categories_repo.update_name(target_id, value)
    pending_repo.clear(require_user(update).id)
    await require_message(update).reply_text(f'Name updated to "{esc(value)}".')
    await show_category_detail(update, context, target_id)


async def _apply_cat_edit_desc(update, context, target_id, context_id, raw) -> None:
    result = _validate_description(raw)
    if isinstance(result, Invalid):
        await _reprompt_after_invalid(update, context, result.message)
        return
    value, message = result.value, result.preview
    categories_repo.update_description(target_id, value)
    pending_repo.clear(require_user(update).id)
    await require_message(update).reply_text(f"Description updated: {esc(message)}")
    await show_category_detail(update, context, target_id)


async def _apply_cat_edit_sort(update, context, target_id, context_id, raw) -> None:
    result = _validate_sort_order(raw)
    if isinstance(result, Invalid):
        await _reprompt_after_invalid(update, context, result.message)
        return
    value, message = result.value, result.preview
    categories_repo.update_sort_order(target_id, value)
    pending_repo.clear(require_user(update).id)
    await require_message(update).reply_text(f"Sort order updated to {message}.")
    await show_category_detail(update, context, target_id)


async def _apply_prod_create_name(update, context, target_id, context_id, raw) -> None:
    result = _validate_name(raw)
    if isinstance(result, Invalid):
        await _reprompt_after_invalid(update, context, result.message)
        return
    value = result.value
    user_id = require_user(update).id
    new_id = products_repo.create(context_id, value)
    pending_repo.clear(user_id)
    await require_message(update).reply_text(f'Product "{esc(value)}" created.')
    await show_product_detail(update, context, new_id)


async def _apply_prod_edit_name(update, context, target_id, context_id, raw) -> None:
    result = _validate_name(raw)
    if isinstance(result, Invalid):
        await _reprompt_after_invalid(update, context, result.message)
        return
    value = result.value
    products_repo.update_name(target_id, value)
    pending_repo.clear(require_user(update).id)
    await require_message(update).reply_text(f'Name updated to "{esc(value)}".')
    await show_product_detail(update, context, target_id)


async def _apply_prod_edit_desc(update, context, target_id, context_id, raw) -> None:
    result = _validate_description(raw)
    if isinstance(result, Invalid):
        await _reprompt_after_invalid(update, context, result.message)
        return
    value, message = result.value, result.preview
    products_repo.update_description(target_id, value)
    pending_repo.clear(require_user(update).id)
    await require_message(update).reply_text(f"Description updated: {esc(message)}")
    await show_product_detail(update, context, target_id)


async def _apply_prod_edit_sort(update, context, target_id, context_id, raw) -> None:
    result = _validate_sort_order(raw)
    if isinstance(result, Invalid):
        await _reprompt_after_invalid(update, context, result.message)
        return
    value, message = result.value, result.preview
    products_repo.update_sort_order(target_id, value)
    pending_repo.clear(require_user(update).id)
    await require_message(update).reply_text(f"Sort order updated to {message}.")
    await show_product_detail(update, context, target_id)


async def _apply_var_create_label(update, context, target_id, context_id, raw) -> None:
    result = _validate_name(raw)
    if isinstance(result, Invalid):
        await _reprompt_after_invalid(update, context, result.message)
        return
    value = result.value
    pending_repo.set(
        require_user(update).id, "var_create_price", context_id=context_id, pending_value=value
    )
    await _prompt(
        update,
        context,
        f'What\'s the price for "{esc(value)}"? Send whole Stars, e.g. 75 or ⭐75 (1–100000).',
    )


async def _apply_var_create_price(update, context, target_id, context_id, raw) -> None:
    result = _validate_price(raw)
    if isinstance(result, Invalid):
        await _reprompt_after_invalid(update, context, result.message)
        return
    cents = result.value
    user_id = require_user(update).id
    pending = pending_repo.get(user_id)
    label = pending["pending_value"] if pending else "?"
    # Third step: pick the delivery kind with buttons. The label and price ride
    # along in pending_value as "{price}:{label}" (the label may contain ":").
    pending_repo.set(
        user_id, "var_create_kind", context_id=context_id, pending_value=f"{cents}:{label}"
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("File", callback_data=cb("adm", "va", "ck", "f")),
                InlineKeyboardButton("Keys", callback_data=cb("adm", "va", "ck", "k")),
                InlineKeyboardButton("Text", callback_data=cb("adm", "va", "ck", "t")),
            ],
            [_cancel_button()],
        ]
    )
    text = (
        f'How is "{esc(label)}" ({stars(cents)}) delivered? This cannot be changed later.\n\n'
        "File — an uploaded file. Keys — one licence key per unit. Text — a message you write."
    )
    await show_screen(update, context, text=text, keyboard=keyboard)


async def var_create_kind(
    update: Update, context: ContextTypes.DEFAULT_TYPE, kind_code: str
) -> None:
    query = require_query(update)
    user_id = require_user(update).id
    pending = pending_repo.get(user_id)
    kind = _KIND_BY_CODE.get(kind_code)
    if pending is None or pending["action"] != "var_create_kind" or kind is None:
        await query.answer(_EXPIRED_MENU_ALERT, show_alert=True)
        return
    price_text, _, label = (pending["pending_value"] or "").partition(":")
    product_id = pending["context_id"]
    variant_id = variants_repo.create(product_id, label, int(price_text), kind)
    pending_repo.clear(user_id)
    await show_variant_detail(update, context, variant_id)


async def var_delivery_text_prompt(
    update: Update, context: ContextTypes.DEFAULT_TYPE, variant_id: int
) -> None:
    variant = variants_repo.get(variant_id)
    if variant is None or variant["delivery_kind"] != "text":
        await require_query(update).answer(_EXPIRED_MENU_ALERT, show_alert=True)
        return
    pending_repo.set(require_user(update).id, "var_delivery_text", target_id=variant_id)
    await _prompt(update, context, _DELIVERY_TEXT_PROMPT)


async def _apply_var_delivery_text(update, context, target_id, context_id, raw) -> None:
    result = admin_common.validate_delivery_text(raw)
    if isinstance(result, Invalid):
        await _reprompt_after_invalid(update, context, result.message)
        return
    value = result.value
    variants_repo.set_delivery_text(target_id, value)
    pending_repo.clear(require_user(update).id)
    await require_message(update).reply_text("Delivery text saved.")
    await show_variant_detail(update, context, target_id)


async def _apply_var_edit_label(update, context, target_id, context_id, raw) -> None:
    result = _validate_name(raw)
    if isinstance(result, Invalid):
        await _reprompt_after_invalid(update, context, result.message)
        return
    value = result.value
    variants_repo.update_label(target_id, value)
    pending_repo.clear(require_user(update).id)
    await require_message(update).reply_text(f'Label updated to "{esc(value)}".')
    await show_variant_detail(update, context, target_id)


async def _apply_var_edit_price(update, context, target_id, context_id, raw) -> None:
    result = _validate_price(raw)
    if isinstance(result, Invalid):
        await _reprompt_after_invalid(update, context, result.message)
        return
    cents, message = result.value, result.preview
    variants_repo.update_price(target_id, cents)
    pending_repo.clear(require_user(update).id)
    await require_message(update).reply_text(f"Price updated to {message}.")
    await show_variant_detail(update, context, target_id)


async def _apply_var_edit_sort(update, context, target_id, context_id, raw) -> None:
    result = _validate_sort_order(raw)
    if isinstance(result, Invalid):
        await _reprompt_after_invalid(update, context, result.message)
        return
    value, message = result.value, result.preview
    variants_repo.update_sort_order(target_id, value)
    pending_repo.clear(require_user(update).id)
    await require_message(update).reply_text(f"Sort order updated to {message}.")
    await show_variant_detail(update, context, target_id)


_PENDING_HANDLERS = {
    "cat_create_name": _apply_cat_create_name,
    "cat_edit_name": _apply_cat_edit_name,
    "cat_edit_desc": _apply_cat_edit_desc,
    "cat_edit_sort": _apply_cat_edit_sort,
    "prod_create_name": _apply_prod_create_name,
    "prod_edit_name": _apply_prod_edit_name,
    "prod_edit_desc": _apply_prod_edit_desc,
    "prod_edit_sort": _apply_prod_edit_sort,
    "var_create_label": _apply_var_create_label,
    "var_create_price": _apply_var_create_price,
    "var_edit_label": _apply_var_edit_label,
    "var_edit_price": _apply_var_edit_price,
    "var_edit_sort": _apply_var_edit_sort,
    "var_delivery_text": _apply_var_delivery_text,
    "set_value": admin_settings.apply_set_value,
}


# ---------------------------------------------------------------------------
# Top-level "adm:" router
# ---------------------------------------------------------------------------


@owner_only
async def route(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = require_query(update)
    parts = parse_callback(require_data(query), range(2, 8))
    if parts is None:
        await query.answer(_EXPIRED_MENU_ALERT, show_alert=True)
        return
    resource = parts[1]

    if resource == "home":
        await dashboard(update, context)
    elif resource == "o":
        await query.answer("Orders: coming soon.", show_alert=True)
    elif resource == "set":
        await admin_settings.route(update, context, parts)
    elif resource == "cancel":
        await cancel_pending(update, context)
    elif resource == "cat":
        await route_category(update, context, parts)
    elif resource == "pr":
        await route_product(update, context, parts)
    elif resource == "va":
        await route_variant(update, context, parts)
    else:
        await query.answer("This menu has expired.", show_alert=True)
