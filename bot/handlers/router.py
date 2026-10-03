"""The single free-text MessageHandler. Routing order: an owner with a
pending admin input goes to the admin branch first; everything else — the
customer branches below — is unchanged, so the owner can still browse and
buy as a customer whenever nothing is pending. Looks up the flag set by the
hash-prompt screen and, if it names an order still eligible to receive a
hash, treats the message as a transaction hash; otherwise nudges the customer
back to the menu. Phase 4B adds the shipping-address branch to this same
dispatch — keep it structured so a new elif fits cleanly.
"""

from __future__ import annotations

import logging
import re
import sqlite3

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from ..config import CONFIG
from ..i18n import t
from ..keyboards import MENU_ACTION_BY_LABEL, cb, main_reply_keyboard
from ..render import bold, esc, usd
from ..render import code as render_code
from ..repo import admin_pending as admin_pending_repo
from ..repo import customers as customers_repo
from ..repo import orders as orders_repo
from ..repo import payment_methods as payment_methods_repo
from ..ui import show_screen
from . import admin_catalogue, catalogue, shipping
from .common import get_lang, render_main_menu, touch_customer

logger = logging.getLogger(__name__)

MAX_HASH_LEN = 200
MIN_HASH_LIKE_LEN = 40
HASH_ELIGIBLE_STATUSES = ("awaiting_payment", "rejected")

# Deliberately loose — a routing heuristic, not validation. Covers hex,
# base58, base64, and 0x-prefixed hex (0 and x are already alphanumeric, so
# no special case is needed for the prefix).
_HASH_LIKE_RE = re.compile(r"^[A-Za-z0-9+/=]+$")


def _looks_like_hash(text: str) -> bool:
    if len(text.split()) != 1:
        return False
    if not (MIN_HASH_LIKE_LEN <= len(text) <= MAX_HASH_LEN):
        return False
    return bool(_HASH_LIKE_RE.match(text))


async def _restore_reply_keyboard(
    update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str
) -> None:
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=t("common.menu_restored", lang),
        reply_markup=main_reply_keyboard(lang),
    )


async def _dispatch_menu_button(
    update: Update, context: ContextTypes.DEFAULT_TYPE, label: str
) -> bool:
    """Persistent-reply-keyboard taps arrive here as plain text messages, so
    they're matched by exact string only — never substring/startswith/
    case-fold — before any branch below gets a chance at the message.
    MENU_ACTION_BY_LABEL covers all six labels across both languages (see
    keyboards.py), so a customer who just switched language and still has
    the other language's keyboard cached doesn't get a dead button. Every
    label contains a space, so none can ever be mistaken for a transaction
    hash (_looks_like_hash requires a single whitespace-free token); none
    can exactly equal a shipping address either (handle_address_text
    requires 2+ lines and these are single-line strings) — a longer address
    that merely contains a label as a substring doesn't match here and falls
    through to the address branch normally, as it should. Returns True if
    the label matched and the update was handled.

    cart and checkout are imported locally rather than at module level:
    checkout.py imports this module, so a module-level import here would be
    circular.
    """
    from . import cart as cart_handlers
    from . import checkout

    action = MENU_ACTION_BY_LABEL.get(label)
    if action == "browse":
        await catalogue.show_categories(update, context)
    elif action == "cart":
        await cart_handlers.show_cart(update, context)
    elif action == "orders":
        await checkout.show_order_list(update, context, 0)
    else:
        return False
    return True


async def route_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user or not update.message.text:
        return
    touch_customer(update)

    raw = update.message.text.strip()

    if await _dispatch_menu_button(update, context, raw):
        return

    if update.effective_user.id == CONFIG.owner_id:
        pending = admin_pending_repo.get(update.effective_user.id)
        if pending is not None:
            await admin_catalogue.handle_pending_input(update, context, pending)
            if admin_pending_repo.get(update.effective_user.id) is None:
                # The input was applied, or the action was unrecognized and
                # fell back to the dashboard — either way nothing more is
                # expected to be typed. If validation failed instead, the
                # pending row is still there and the keyboard stays hidden.
                await _restore_reply_keyboard(update, context, get_lang(update.effective_user.id))
            return
        # No pending admin input: fall through to the customer branches below
        # so the owner can still test the shop with their own account.

    if context.user_data.get("awaiting_checkout_address"):
        # Phase 6: location collection now happens before payment, before any
        # order exists — checked ahead of the hash/shipping branches below,
        # which all assume an order (the active one, or the flagged one) to
        # act on. checkout is imported locally: it imports this module.
        from . import checkout

        await checkout.handle_checkout_address_text(update, context)
        return

    order_id = context.user_data.get("awaiting_hash_order_id")
    order = orders_repo.get(order_id) if order_id is not None else None

    if order is not None and order["status"] in HASH_ELIGIBLE_STATUSES:
        await _handle_hash_text(update, context, order)
        return

    active_order = orders_repo.get_active_for_user(update.effective_user.id)

    # Restart-survival fallback: the prompt flag lives in context.user_data
    # and is lost on restart. If the customer has an order still eligible for
    # a hash and this text is shaped like one, treat it as an attempt anyway.
    if (
        _looks_like_hash(raw)
        and active_order is not None
        and active_order["status"] in HASH_ELIGIBLE_STATUSES
    ):
        await _handle_hash_text(update, context, active_order)
        return

    # Shipping-address branch, after the hash branches: the next message from
    # a customer with an order in awaiting_shipping is their address.
    if active_order is not None and active_order["status"] == "awaiting_shipping":
        await shipping.handle_address_text(update, context, active_order)
        return

    await _nudge(update, context)


async def _nudge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = get_lang(update.effective_user.id)
    menu_text, keyboard = render_main_menu(update.effective_user.id, lang)
    await update.message.reply_text(
        t("common.nudge", lang, menu=menu_text),
        reply_markup=keyboard,
    )


async def _handle_hash_text(update: Update, context: ContextTypes.DEFAULT_TYPE, order) -> None:
    lang = get_lang(update.effective_user.id)
    raw = update.message.text.strip()

    if len(raw) > MAX_HASH_LEN:
        await update.message.reply_text(t("checkout.hash_too_long", lang, max=MAX_HASH_LEN))
        return

    pm = payment_methods_repo.get(order["payment_method"])
    regex = pm["tx_hash_regex"] if pm else None
    if regex and not re.match(regex, raw):
        context.user_data["pending_hash"] = raw
        network = esc(pm["network"]) if pm else "expected"
        text = t("checkout.hash_mismatch", lang, network=network, hash=render_code(raw))
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        t("checkout.send_anyway_button", lang),
                        callback_data=cb("co", "hashok", order["id"]),
                    )
                ],
                [
                    InlineKeyboardButton(
                        t("checkout.try_again_button", lang),
                        callback_data=cb("co", "hash", order["id"]),
                    )
                ],
                [
                    InlineKeyboardButton(
                        t("checkout.back_to_payment_details_button", lang),
                        callback_data=cb("co", "pay", order["payment_method"]),
                    )
                ],
            ]
        )
        await update.message.reply_text(text, reply_markup=keyboard)
        return

    await finalize_hash_submission(update, context, order, raw)


async def finalize_hash_submission(
    update: Update, context: ContextTypes.DEFAULT_TYPE, order, tx_hash: str
) -> None:
    """Shared by the plain-text path above and the "Send it anyway" callback
    in handlers/checkout.py — either can supply a hash for the same order."""
    lang = get_lang(update.effective_user.id)
    order_id = order["id"]
    try:
        orders_repo.submit_tx_hash(order_id, tx_hash)
    except sqlite3.IntegrityError:
        await _respond(update, context, t("checkout.duplicate_hash", lang))
        return

    context.user_data.pop("awaiting_hash_order_id", None)
    context.user_data.pop("pending_hash", None)

    order_heading = bold(f"Order #{order_id}")
    text = t("checkout.hash_submitted", lang, order_heading=order_heading)
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t("common.my_orders_button", lang), callback_data=cb("ord", "list", 0)
                )
            ],
            [
                InlineKeyboardButton(
                    t("common.back_to_menu_button", lang), callback_data=cb("menu", "show")
                )
            ],
        ]
    )
    await _respond(update, context, text, keyboard)
    # The confirmation message above already used its one reply_markup slot
    # for the inline buttons (edited or sent fresh, either way) — the reply
    # keyboard, hidden since the hash prompt, needs its own follow-up.
    await _restore_reply_keyboard(update, context, lang)
    await _notify_owner(context, order_id)


async def _respond(update, context, text, keyboard=None):
    lang = get_lang(update.effective_user.id)
    keyboard = keyboard or InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t("common.back_to_menu_button", lang), callback_data=cb("menu", "show")
                )
            ]
        ]
    )
    if update.callback_query is not None:
        await show_screen(update, context, text=text, keyboard=keyboard)
    else:
        await update.message.reply_text(text, reply_markup=keyboard)


async def _notify_owner(context: ContextTypes.DEFAULT_TYPE, order_id: int) -> None:
    try:
        order, items = orders_repo.get_with_items(order_id)
        customer = customers_repo.get(order["user_id"])
        pm = payment_methods_repo.get(order["payment_method"])

        if customer and customer["username"]:
            who = f"@{esc(customer['username'])} (id {order['user_id']})"
        else:
            who = f"id {order['user_id']}"

        lines = [
            bold(f"New transaction hash — Order #{order_id}"),
            f"Customer: {who}",
            "",
        ]
        for item in items:
            item_name = f"{item['product_name']} {item['variant_label']}".strip()
            lines.append(
                f"{esc(item_name)} — {item['quantity']} × "
                f"{usd(item['unit_price_usd'])} = {usd(item['line_total_usd'])}"
            )
        lines += ["", bold(f"Total: {usd(order['total_usd'])}")]
        lines += ["", f"Asset: {esc(pm['asset'])} ({esc(pm['network'])})"]
        lines += ["", f"Hash: {render_code(order['tx_hash'])}"]

        if pm["explorer_url"]:
            link = esc(pm["explorer_url"].replace("{tx}", order["tx_hash"]))
            lines.append(f'<a href="{link}">View on block explorer</a>')

        text = "\n".join(lines)
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "Verify", callback_data=cb("adm", "ord", "verify", order_id)
                    ),
                    InlineKeyboardButton(
                        "Reject", callback_data=cb("adm", "ord", "reject", order_id)
                    ),
                ]
            ]
        )
        await context.bot.send_message(CONFIG.owner_id, text, reply_markup=keyboard)
    except Exception:
        logger.error("Failed to notify owner about order #%s", order_id, exc_info=True)


# ---------------------------------------------------------------------------
# Payment proof by screenshot (Phase 6) — the single filters.PHOTO
# MessageHandler registered in main.py. A photo is accepted only when the
# sender has an active order eligible for proof (same statuses as the hash
# path); anything else gets the same nudge unrecognized text gets.
# ---------------------------------------------------------------------------


async def route_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user or not update.message.photo:
        return
    touch_customer(update)

    active_order = orders_repo.get_active_for_user(update.effective_user.id)
    if active_order is None or active_order["status"] not in HASH_ELIGIBLE_STATUSES:
        await _nudge(update, context)
        return

    file_id = update.message.photo[-1].file_id  # largest PhotoSize is last
    await _finalize_photo_submission(update, context, active_order, file_id)


async def _finalize_photo_submission(
    update: Update, context: ContextTypes.DEFAULT_TYPE, order, file_id: str
) -> None:
    lang = get_lang(update.effective_user.id)
    order_id = order["id"]
    orders_repo.submit_photo_proof(order_id, file_id)

    context.user_data.pop("awaiting_hash_order_id", None)
    context.user_data.pop("pending_hash", None)

    order_heading = bold(f"Order #{order_id}")
    text = t("checkout.hash_submitted", lang, order_heading=order_heading)
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t("common.my_orders_button", lang), callback_data=cb("ord", "list", 0)
                )
            ],
            [
                InlineKeyboardButton(
                    t("common.back_to_menu_button", lang), callback_data=cb("menu", "show")
                )
            ],
        ]
    )
    await update.message.reply_text(text, reply_markup=keyboard)
    await _restore_reply_keyboard(update, context, lang)
    await _notify_owner_photo(context, order_id, file_id)


async def _notify_owner_photo(
    context: ContextTypes.DEFAULT_TYPE, order_id: int, file_id: str
) -> None:
    """Sends the photo itself (not just a text notification), with order
    details as the caption. Kept deliberately short — Telegram caps photo
    captions at 1024 characters (well under the 4096 a text message allows),
    and the photo failing to arrive at all would be worse than a caption
    that omits the line-by-line item breakdown the hash notification has;
    the full order is one tap away via the order card."""
    try:
        order = orders_repo.get(order_id)
        customer = customers_repo.get(order["user_id"])
        pm = payment_methods_repo.get(order["payment_method"])

        if customer and customer["username"]:
            who = f"@{esc(customer['username'])} (id {order['user_id']})"
        else:
            who = f"id {order['user_id']}"

        lines = [
            bold(f"New payment screenshot — Order #{order_id}"),
            f"Customer: {who}",
            "",
            bold(f"Total: {usd(order['total_usd'])}"),
            f"Asset: {esc(pm['asset'])} ({esc(pm['network'])})" if pm else "Asset: (unknown)",
        ]
        caption = "\n".join(lines)
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "Verify", callback_data=cb("adm", "ord", "verify", order_id)
                    ),
                    InlineKeyboardButton(
                        "Reject", callback_data=cb("adm", "ord", "reject", order_id)
                    ),
                ]
            ]
        )
        await context.bot.send_photo(
            CONFIG.owner_id, photo=file_id, caption=caption, reply_markup=keyboard
        )
    except Exception:
        logger.error(
            "Failed to notify owner about photo proof for order #%s", order_id, exc_info=True
        )
