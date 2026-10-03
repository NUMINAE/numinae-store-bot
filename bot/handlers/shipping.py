"""Customer shipping-address collection: prompt -> free-text address ->
confirmation -> repo.orders.set_shipping() (which moves the order to
'confirmed') -> owner notification with the full order, ready to pack.

Phase 6 moved address collection before payment for new orders (see
handlers/checkout.py) — this module now serves only pre-Phase-6 orders still
sitting in 'awaiting_shipping'. Left fully intact for exactly that reason.

The typed-but-unconfirmed address lives in context.user_data between
"customer sends it" and "customer taps Confirm" — mirroring the existing
"pending_hash" / "send it anyway" pattern in handlers/router.py — since
nothing should be written to the order until Confirm, and it's discarded
entirely if the customer taps Re-enter or never confirms.
"""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import ContextTypes

from ..config import CONFIG
from ..i18n import t
from ..keyboards import cb, main_reply_keyboard, parse_callback, parse_cb_id
from ..render import bold, esc, pre, usd
from ..render import code as render_code
from ..repo import customers as customers_repo
from ..repo import orders as orders_repo
from ..repo import payment_methods as payment_methods_repo
from ..security import get_owned_order
from ..ui import show_screen
from .common import get_lang as _get_lang

logger = logging.getLogger(__name__)

MIN_ADDRESS_LEN = 15
MIN_ADDRESS_LINES = 2
MAX_ADDRESS_LEN = 1000

ADDRESS_TEMPLATE = "Full name\nPhone number\nStreet address\nCity and postal code\nCountry"


def _prompt_content(order_id: int, customer, lang: str) -> tuple[str, InlineKeyboardMarkup]:
    template = t("shipping.full_template", lang)
    text = t("shipping.prompt", lang, template=pre(template))
    buttons = []
    if customer and customer["last_shipping_text"]:
        buttons.append(
            [
                InlineKeyboardButton(
                    t("shipping.use_previous_button", lang),
                    callback_data=cb("sh", "useprev", order_id),
                )
            ]
        )
    # Routed through "sh:cancel" rather than "ord:list" directly — this is
    # the only way to leave the address flow without confirming, so it also
    # needs to restore the reply keyboard on the way out.
    buttons.append(
        [
            InlineKeyboardButton(
                t("shipping.my_orders_button", lang), callback_data=cb("sh", "cancel", order_id)
            )
        ]
    )
    return text, InlineKeyboardMarkup(buttons)


async def send_address_prompt(context: ContextTypes.DEFAULT_TYPE, order_id: int) -> bool:
    """Admin-triggered send (on verification, or 'Resend address request') —
    there's no customer Update to reply through, so this composes and sends a
    fresh message directly. Returns False (and logs at ERROR) if delivery
    fails, so the caller can surface "could not notify customer"."""
    order = orders_repo.get(order_id)
    if order is None:
        return False
    lang = _get_lang(order["user_id"])
    customer = customers_repo.get(order["user_id"])
    text, keyboard = _prompt_content(order_id, customer, lang)
    try:
        await context.bot.send_message(chat_id=order["user_id"], text=text, reply_markup=keyboard)
    except Exception:
        logger.error(
            "Failed to send address prompt to customer #%s for order #%s",
            order["user_id"],
            order_id,
            exc_info=True,
        )
        return False

    # The prompt above already used its one reply_markup slot for the inline
    # buttons — a short follow-up carries the keyboard removal. A failure
    # here doesn't count as "prompt not delivered": the customer got it.
    try:
        await context.bot.send_message(
            chat_id=order["user_id"],
            text=t("common.type_below", lang),
            reply_markup=ReplyKeyboardRemove(),
        )
    except Exception:
        logger.error(
            "Failed to remove reply keyboard for customer #%s for order #%s",
            order["user_id"],
            order_id,
            exc_info=True,
        )
    return True


async def show_address_prompt(
    update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: int
) -> None:
    """Customer-triggered reopen — 'sh:prompt:{id}' or 'Re-enter'."""
    lang = _get_lang(update.effective_user.id)
    order = await get_owned_order(update, order_id)
    if order is None:
        return
    if order["status"] != "awaiting_shipping":
        await update.callback_query.answer(
            t("shipping.cannot_accept_address", lang), show_alert=True
        )
        return
    customer = customers_repo.get(order["user_id"])
    text, keyboard = _prompt_content(order_id, customer, lang)
    await show_screen(update, context, text=text, keyboard=keyboard)
    # show_screen above edits the existing message, and ReplyKeyboardRemove
    # can only attach to a new one — a short follow-up carries it instead.
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=t("common.type_below", lang),
        reply_markup=ReplyKeyboardRemove(),
    )


def _confirmation_content(
    order_id: int, address: str, lang: str
) -> tuple[str, InlineKeyboardMarkup]:
    text = t("shipping.confirm_heading", lang) + "\n\n" + pre(address)
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t("shipping.confirm_button", lang), callback_data=cb("sh", "confirm", order_id)
                )
            ],
            [
                InlineKeyboardButton(
                    t("shipping.reenter_button", lang), callback_data=cb("sh", "reenter", order_id)
                )
            ],
        ]
    )
    return text, keyboard


async def handle_address_text(update: Update, context: ContextTypes.DEFAULT_TYPE, order) -> None:
    """Called from handlers/router.py when the customer has an active order
    in 'awaiting_shipping' and sends free text."""
    lang = _get_lang(update.effective_user.id)
    raw = update.message.text.strip()
    lines = [line for line in raw.splitlines() if line.strip()]

    if len(raw) > MAX_ADDRESS_LEN:
        await update.message.reply_text(t("shipping.too_long", lang, max=MAX_ADDRESS_LEN))
        return

    if len(raw) < MIN_ADDRESS_LEN or len(lines) < MIN_ADDRESS_LINES:
        template = t("shipping.full_template", lang)
        await update.message.reply_text(t("shipping.too_short", lang, template=pre(template)))
        return

    context.user_data["pending_shipping_text"] = raw
    text, keyboard = _confirmation_content(order["id"], raw, lang)
    await update.message.reply_text(text, reply_markup=keyboard)


async def use_previous_address(
    update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: int
) -> None:
    lang = _get_lang(update.effective_user.id)
    order = await get_owned_order(update, order_id)
    if order is None:
        return
    if order["status"] != "awaiting_shipping":
        await update.callback_query.answer(
            t("shipping.cannot_accept_address", lang), show_alert=True
        )
        return
    customer = customers_repo.get(order["user_id"])
    if customer is None or not customer["last_shipping_text"]:
        await update.callback_query.answer(t("shipping.no_previous_alert", lang), show_alert=True)
        return
    context.user_data["pending_shipping_text"] = customer["last_shipping_text"]
    text, keyboard = _confirmation_content(order_id, customer["last_shipping_text"], lang)
    await show_screen(update, context, text=text, keyboard=keyboard)


async def confirm_address(
    update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: int
) -> None:
    lang = _get_lang(update.effective_user.id)
    order = await get_owned_order(update, order_id)
    if order is None:
        return
    pending_text = context.user_data.get("pending_shipping_text")

    if order["status"] != "awaiting_shipping" or not pending_text:
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        t("shipping.enter_address_button", lang),
                        callback_data=cb("sh", "prompt", order_id),
                    )
                ]
            ]
        )
        await show_screen(
            update, context, text=t("shipping.request_expired", lang), keyboard=keyboard
        )
        return

    orders_repo.set_shipping(order_id, pending_text)
    context.user_data.pop("pending_shipping_text", None)

    order_heading = bold(t("checkout.order_heading", lang, id=order_id))
    text = t("shipping.confirmed", lang, order_heading=order_heading)
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
    await show_screen(update, context, text=text, keyboard=keyboard)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=t("common.menu_restored", lang),
        reply_markup=main_reply_keyboard(lang),
    )

    await _notify_owner_confirmed(context, order_id)


async def _notify_owner_confirmed(context: ContextTypes.DEFAULT_TYPE, order_id: int) -> None:
    """Owner-facing — stays in English."""
    try:
        order, items = orders_repo.get_with_items(order_id)
        customer = customers_repo.get(order["user_id"])
        pm = payment_methods_repo.get(order["payment_method"])

        who = (
            f"@{esc(customer['username'])} (id {order['user_id']})"
            if customer and customer["username"]
            else f"id {order['user_id']}"
        )

        lines = [bold(f"Order #{order_id} confirmed — ready to pack"), f"Customer: {who}", ""]
        for item in items:
            item_name = f"{item['product_name']} {item['variant_label']}".strip()
            lines.append(
                f"{esc(item_name)} — {item['quantity']} × "
                f"{usd(item['unit_price_usd'])} = {usd(item['line_total_usd'])}"
            )
        lines += ["", bold(f"Total: {usd(order['total_usd'])}")]
        lines += [
            "",
            f"Payment: {esc(pm['asset'])} ({esc(pm['network'])})" if pm else "Payment: (unknown)",
        ]
        if order["tx_hash"]:
            lines += ["", f"Hash: {render_code(order['tx_hash'])}"]
        lines += ["", bold("Shipping address:"), pre(order["shipping_text"])]

        text = "\n".join(lines)
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("View order", callback_data=cb("adm", "o", "v", order_id))]]
        )
        await context.bot.send_message(CONFIG.owner_id, text, reply_markup=keyboard)
    except Exception:
        logger.error("Failed to notify owner about confirmed order #%s", order_id, exc_info=True)


async def cancel_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: int) -> None:
    """'My orders' tapped from the address prompt — the only way to leave
    the shipping flow without confirming. order_id is unused: this doesn't
    read or write the order, it just navigates away and restores the
    keyboard that's been hidden since the prompt was shown."""
    lang = _get_lang(update.effective_user.id)
    from . import checkout

    await checkout.show_order_list(update, context, 0)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=t("common.menu_restored", lang),
        reply_markup=main_reply_keyboard(lang),
    )


async def route(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    lang = _get_lang(update.effective_user.id) if update.effective_user else "en"
    parts = parse_callback(query.data, 3)
    order_id = parse_cb_id(parts[2]) if parts else None
    if order_id is None:
        await query.answer(t("common.expired_menu", lang), show_alert=True)
        return
    action = parts[1]

    if action == "prompt":
        await show_address_prompt(update, context, order_id)
    elif action == "useprev":
        await use_previous_address(update, context, order_id)
    elif action == "confirm":
        await confirm_address(update, context, order_id)
    elif action == "reenter":
        await show_address_prompt(update, context, order_id)
    elif action == "cancel":
        await cancel_prompt(update, context, order_id)
    else:
        await query.answer(t("common.expired_menu", lang), show_alert=True)
