"""Checkout: review -> create the order -> hand over to the payment provider.
Registered under the "co:" callback namespace.

Every order reached by id goes through get_owned_order, and the demo "Pay"
button runs the very same payability check and completion path as a real
payment (handlers/payments.process_payment), so the mock cannot do anything a
real payment couldn't."""

from __future__ import annotations

import logging
import sqlite3
import uuid

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from ..i18n import t
from ..keyboards import cb, parse_callback, parse_cb_id
from ..limits import MAX_ORDER_STARS
from ..payments.checks import REJECTION_MESSAGE_KEY, check_payable
from ..render import bold, esc, stars
from ..repo import cart as cart_repo
from ..repo import orders as orders_repo
from ..repo import products as products_repo
from ..security import get_owned_order
from ..tg import require_data, require_query, require_user
from ..ui import show_screen
from . import payments
from .common import get_lang, touch_customer

logger = logging.getLogger(__name__)

MOCK_MODE = "mock"
RESERVATION_EXTENSION_MINUTES = payments.RESERVATION_EXTENSION_MINUTES


def _button(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=data)


def _keyboard(*rows: InlineKeyboardButton) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[row] for row in rows])


def _chat_id(update: Update) -> int:
    chat = update.effective_chat
    return chat.id if chat is not None else require_user(update).id


async def _expired(update: Update) -> None:
    lang = get_lang(require_user(update).id)
    await require_query(update).answer(t("common.expired_menu", lang), show_alert=True)


def _back_to_cart(lang: str) -> InlineKeyboardButton:
    return _button(t("cart.back_to_cart_button", lang), cb("cart", "view"))


# ---------------------------------------------------------------------------
# Entry: guards and the review screen
# ---------------------------------------------------------------------------


async def show_open_order(
    update: Update, context: ContextTypes.DEFAULT_TYPE, order: sqlite3.Row, lang: str
) -> None:
    text = t("checkout.open_order", lang, id=order["id"], minutes=orders_repo.minutes_left(order))
    keyboard = _keyboard(
        _button(t("checkout.resume_button", lang), cb("co", "resume", order["id"])),
        _button(t("pay.cancel_order_button", lang), cb("co", "cancel", order["id"])),
        _back_to_cart(lang),
    )
    await show_screen(update, context, text=text, keyboard=keyboard)


async def _show_empty(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str) -> None:
    keyboard = _keyboard(
        _button(t("common.browse_button", lang), cb("cat", "cats")),
        _back_to_cart(lang),
    )
    await show_screen(update, context, text=t("checkout.empty", lang), keyboard=keyboard)


async def _show_too_large(
    update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str, total: int
) -> None:
    text = t("checkout.too_large", lang, total=total, limit=MAX_ORDER_STARS)
    await show_screen(update, context, text=text, keyboard=_keyboard(_back_to_cart(lang)))


async def checkout_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    touch_customer(update)
    user_id = require_user(update).id
    lang = get_lang(user_id)
    provider = payments.get_provider(context)

    open_order = orders_repo.get_open_for_user(user_id)
    if open_order is not None:
        await show_open_order(update, context, open_order, lang)
        return

    lines = cart_repo.list_lines(user_id)
    purchasable = [ln for ln in lines if products_repo.is_variant_available(ln)]
    if not purchasable:
        await _show_empty(update, context, lang)
        return

    total = sum(ln["line_total"] for ln in purchasable)
    if total > MAX_ORDER_STARS:
        await _show_too_large(update, context, lang, total)
        return

    body = [bold(t("checkout.review_heading", lang)), ""]
    for ln in purchasable:
        name = esc(f"{ln['product_name']} — {ln['variant_label']}")
        body.append(f"{name} × {ln['quantity']} — {stars(ln['line_total'])}")
    body += ["", bold(t("checkout.total", lang, total=stars(total)))]
    dropped = len(lines) - len(purchasable)
    if dropped:
        body.append(t("checkout.dropped_note", lang, count=dropped))
    if provider.mode == MOCK_MODE:
        body += ["", t("checkout.demo_notice", lang)]
    keyboard = _keyboard(
        _button(t("checkout.pay_button", lang, total=total), cb("co", "pay")),
        _back_to_cart(lang),
    )
    await show_screen(update, context, text="\n".join(body), keyboard=keyboard)


# ---------------------------------------------------------------------------
# Creating the order and presenting payment
# ---------------------------------------------------------------------------


async def _present(
    update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: int, lang: str, note: str = ""
) -> bool:
    """Show the payment screen for a pending order. If the provider fails, the
    order is cancelled (keys released, cart restored) so the customer isn't
    stuck. Returns whether the payment was presented."""
    provider = payments.get_provider(context)
    order, items = orders_repo.get_with_items(order_id)
    if order is None:
        return False
    try:
        await provider.present_checkout(context.bot, _chat_id(update), order, items, lang)
    except Exception:
        logger.error("Could not present checkout for order %s", order_id, exc_info=True)
        try:
            orders_repo.cancel_order(order_id, "customer")
        except orders_repo.OrderError:
            logger.error(
                "Could not cancel order %s after a failed checkout", order_id, exc_info=True
            )
        await show_screen(
            update,
            context,
            text=t("checkout.present_failed", lang),
            keyboard=_keyboard(_back_to_cart(lang)),
        )
        return False
    text = (note + "\n" if note else "") + t("checkout.presented", lang, id=order_id)
    keyboard = _keyboard(_button(t("common.back_to_menu_button", lang), cb("menu", "show")))
    await show_screen(update, context, text=text, keyboard=keyboard)
    return True


async def checkout_pay(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    touch_customer(update)
    user_id = require_user(update).id
    lang = get_lang(user_id)
    provider = payments.get_provider(context)
    try:
        created = orders_repo.create_order(user_id, provider.mode)
    except orders_repo.OpenOrderExists as exc:
        existing = orders_repo.get(exc.order_id)
        if existing is None:  # vanished between the failure and this read
            await _show_empty(update, context, lang)
        else:
            await show_open_order(update, context, existing, lang)
        return
    except orders_repo.EmptyCartError:
        await _show_empty(update, context, lang)
        return
    except orders_repo.OrderTooLarge as exc:
        await _show_too_large(update, context, lang, exc.total)
        return
    except orders_repo.InsufficientStock as exc:
        text = t("checkout.stock_short", lang, available=exc.available)
        await show_screen(update, context, text=text, keyboard=_keyboard(_back_to_cart(lang)))
        return

    note = (
        t("checkout.dropped_note", lang, count=created.dropped_count)
        if created.dropped_count
        else ""
    )
    await _present(update, context, created.order_id, lang, note)


# ---------------------------------------------------------------------------
# Resume / cancel
# ---------------------------------------------------------------------------


async def _owned_pending(update: Update, order_id: int, lang: str) -> sqlite3.Row | None:
    """The caller's own order if it can still be acted on; otherwise answers the
    callback and returns None."""
    order = await get_owned_order(update, order_id)
    if order is None:
        return None
    if order["status"] != "pending_payment":
        await require_query(update).answer(t("checkout.cancel_refused", lang), show_alert=True)
        return None
    return order


async def checkout_resume(
    update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: int
) -> None:
    lang = get_lang(require_user(update).id)
    order = await _owned_pending(update, order_id, lang)
    if order is None:
        return
    provider = payments.get_provider(context)
    if order["payment_mode"] != provider.mode:
        # Created under a different payment mode: it can't be paid here.
        orders_repo.cancel_order(order_id, "customer")
        await show_screen(
            update,
            context,
            text=t("checkout.mode_changed", lang),
            keyboard=_keyboard(_back_to_cart(lang)),
        )
        return
    if orders_repo.is_expired(order):
        await require_query(update).answer(t("pay.err_expired", lang), show_alert=True)
        return
    await _present(update, context, order_id, lang)


async def checkout_cancel_ask(
    update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: int
) -> None:
    lang = get_lang(require_user(update).id)
    if await _owned_pending(update, order_id, lang) is None:
        return
    keyboard = InlineKeyboardMarkup(
        [
            [
                _button(t("checkout.yes_cancel_button", lang), cb("co", "cancelyes", order_id)),
                _button(t("checkout.keep_button", lang), cb("co", "cancelno", order_id)),
            ]
        ]
    )
    await show_screen(
        update,
        context,
        text=t("checkout.cancel_confirm", lang, id=order_id),
        keyboard=keyboard,
    )


async def checkout_cancel_yes(
    update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: int
) -> None:
    lang = get_lang(require_user(update).id)
    if await _owned_pending(update, order_id, lang) is None:
        return
    try:
        restored = orders_repo.cancel_order(order_id, "customer")
    except orders_repo.IllegalTransitionError:
        await require_query(update).answer(t("checkout.cancel_refused", lang), show_alert=True)
        return
    key = "checkout.cancelled_restored" if restored else "checkout.cancelled"
    keyboard = _keyboard(
        _button(t("catalogue.view_cart_button", lang), cb("cart", "view")),
        _button(t("common.browse_button", lang), cb("cat", "cats")),
    )
    await show_screen(update, context, text=t(key, lang, id=order_id), keyboard=keyboard)


async def checkout_cancel_no(
    update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: int
) -> None:
    lang = get_lang(require_user(update).id)
    order = await _owned_pending(update, order_id, lang)
    if order is not None:
        await show_open_order(update, context, order, lang)


# ---------------------------------------------------------------------------
# The demo payment
# ---------------------------------------------------------------------------


async def checkout_mock_pay(
    update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: int
) -> None:
    """The demo "Pay" button. Refused unless the bot is running in mock mode,
    the order belongs to the caller and was created in mock mode, and the same
    payability check a real payment faces passes; then it completes through the
    same path as a real payment."""
    query = require_query(update)
    user_id = require_user(update).id
    lang = get_lang(user_id)
    provider = payments.get_provider(context)

    if provider.mode != MOCK_MODE:  # (1) unreachable outside demo mode
        await query.answer(t("checkout.mock_unavailable", lang), show_alert=True)
        return
    # Ownership comes before the order-mode check so someone else's order is
    # refused exactly like a nonexistent one.
    order = await get_owned_order(update, order_id)  # (3)
    if order is None:
        return
    if order["payment_mode"] != MOCK_MODE:  # (2)
        await query.answer(t("checkout.mock_unavailable", lang), show_alert=True)
        return

    rejection = check_payable(order, user_id, order["total_stars"], "XTR", provider.mode)
    if rejection is not None:
        await query.answer(t(REJECTION_MESSAGE_KEY[rejection], lang), show_alert=True)
        return

    orders_repo.extend_reservation(order_id, RESERVATION_EXTENSION_MINUTES)
    await payments.process_payment(
        context,
        order_id,
        user_id,
        f"mock_{uuid.uuid4().hex}",
        order["total_stars"],
        "XTR",
    )
    await show_screen(
        update,
        context,
        text=t("checkout.mock_done", lang, id=order_id),
        keyboard=_keyboard(_button(t("common.my_library_button", lang), cb("lib", "list", 0))),
    )


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


async def route(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = require_query(update)
    parts = parse_callback(require_data(query), range(2, 4))
    if parts is None:
        await _expired(update)
        return
    action = parts[1]
    if action == "start":
        await checkout_start(update, context)
        return
    if action == "pay":
        await checkout_pay(update, context)
        return
    order_id = parse_cb_id(parts[2]) if len(parts) == 3 else None
    if order_id is None:
        await _expired(update)
        return
    if action == "resume":
        await checkout_resume(update, context, order_id)
    elif action == "cancel":
        await checkout_cancel_ask(update, context, order_id)
    elif action == "cancelyes":
        await checkout_cancel_yes(update, context, order_id)
    elif action == "cancelno":
        await checkout_cancel_no(update, context, order_id)
    elif action == "mockpay":
        await checkout_mock_pay(update, context, order_id)
    else:
        await _expired(update)
