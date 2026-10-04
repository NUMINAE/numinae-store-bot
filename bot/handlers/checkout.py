"""Checkout: review -> create the order -> hand over to the payment provider.
Registered under the "co:" callback namespace.

Every order reached by id goes through get_owned_order, and the demo "Pay"
button runs the very same payability check and completion path as a real
payment (handlers/payments.process_payment), so the mock cannot do anything a
real payment couldn't.

Screens follow the design language: ui_kit.screen() text, nav_row() last. The
purchase is shown as "Step 1 of 3 · Review", "Step 2 of 3 · Payment" (the
invoice / demo summary) and "Step 3 of 3 · Delivery" (the receipt)."""

from __future__ import annotations

import logging
import sqlite3
import uuid

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from ..i18n import t, tn
from ..keyboards import cb, parse_callback, parse_cb_id
from ..limits import MAX_ORDER_STARS
from ..payments.checks import REJECTION_MESSAGE_KEY, check_payable
from ..render import bold, esc, stars
from ..repo import cart as cart_repo
from ..repo import orders as orders_repo
from ..repo import products as products_repo
from ..security import get_owned_order
from ..summary import summary_lines
from ..tg import require_data, require_query, require_user
from ..ui import show_screen
from ..ui_kit import ICON, SEP_PRICE, nav_row, screen
from . import payments
from .common import touch_customer

logger = logging.getLogger(__name__)

MOCK_MODE = "mock"
RESERVATION_EXTENSION_MINUTES = payments.RESERVATION_EXTENSION_MINUTES


def _button(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=data)


def _chat_id(update: Update) -> int:
    chat = update.effective_chat
    return chat.id if chat is not None else require_user(update).id


async def _expired(update: Update) -> None:
    await require_query(update).answer(t("common.expired_menu"), show_alert=True)


def _notice(icon: str, body: str) -> tuple[str, InlineKeyboardMarkup]:
    """A checkout message screen: what happened, and the way back to the cart."""
    return (
        screen(icon, t("checkout.title"), esc(body)),
        InlineKeyboardMarkup([nav_row(back=cb("cart", "view"))]),
    )


# ---------------------------------------------------------------------------
# Entry: guards and the review screen
# ---------------------------------------------------------------------------


async def show_open_order(
    update: Update, context: ContextTypes.DEFAULT_TYPE, order: sqlite3.Row
) -> None:
    body = t("checkout.open_order", id=order["id"], minutes=orders_repo.minutes_left(order))
    keyboard = InlineKeyboardMarkup(
        [
            [_button(t("checkout.resume_button"), cb("co", "resume", order["id"]))],
            [_button(t("pay.cancel_order_button"), cb("co", "cancel", order["id"]))],
            nav_row(back=cb("cart", "view")),
        ]
    )
    text = screen(ICON.info, t("checkout.title"), esc(body))
    await show_screen(update, context, text=text, keyboard=keyboard)


async def _show_empty(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = InlineKeyboardMarkup(
        [
            [_button(t("common.browse_button"), cb("cat", "cats"))],
            nav_row(back=cb("cart", "view")),
        ]
    )
    text = screen(ICON.warning, t("checkout.title"), esc(t("checkout.empty")))
    await show_screen(update, context, text=text, keyboard=keyboard)


async def _show_too_large(update: Update, context: ContextTypes.DEFAULT_TYPE, total: int) -> None:
    text, keyboard = _notice(
        ICON.warning, t("checkout.too_large", total=total, limit=MAX_ORDER_STARS)
    )
    await show_screen(update, context, text=text, keyboard=keyboard)


async def checkout_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    touch_customer(update)
    user_id = require_user(update).id
    provider = payments.get_provider(context)

    open_order = orders_repo.get_open_for_user(user_id)
    if open_order is not None:
        await show_open_order(update, context, open_order)
        return

    lines = cart_repo.list_lines(user_id)
    purchasable = [ln for ln in lines if products_repo.is_variant_available(ln)]
    if not purchasable:
        await _show_empty(update, context)
        return

    total = sum(ln["line_total"] for ln in purchasable)
    if total > MAX_ORDER_STARS:
        await _show_too_large(update, context, total)
        return

    body = summary_lines(
        purchasable,
        lambda ln: f"{ln['product_name']}{SEP_PRICE}{ln['variant_label']}",
        lambda ln: int(ln["quantity"]),
        lambda ln: int(ln["line_total"]),
    )
    body += ["", bold(esc(t("checkout.total", total=stars(total))))]
    dropped = len(lines) - len(purchasable)
    if dropped:
        body.append(esc(tn("checkout.dropped_note", dropped)))
    if provider.mode == MOCK_MODE:
        body += ["", esc(t("checkout.demo_notice"))]
    keyboard = InlineKeyboardMarkup(
        [
            [_button(t("checkout.pay_button", total=total), cb("co", "pay"))],
            nav_row(back=cb("cart", "view")),
        ]
    )
    text = screen(
        ICON.cart,
        t("checkout.title"),
        "\n".join(body),
        step=(1, 3, t("checkout.step_review")),
    )
    await show_screen(update, context, text=text, keyboard=keyboard)


# ---------------------------------------------------------------------------
# Creating the order and presenting payment
# ---------------------------------------------------------------------------


async def _present(
    update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: int, note: str = ""
) -> bool:
    """Show the payment screen for a pending order. If the provider fails, the
    order is cancelled (keys released, cart restored) so the customer isn't
    stuck. Returns whether the payment was presented."""
    provider = payments.get_provider(context)
    order, items = orders_repo.get_with_items(order_id)
    if order is None:
        return False
    try:
        await provider.present_checkout(context.bot, _chat_id(update), order, items)
    except Exception:
        logger.error("Could not present checkout for order %s", order_id, exc_info=True)
        try:
            orders_repo.cancel_order(order_id, "customer")
        except orders_repo.OrderError:
            logger.error(
                "Could not cancel order %s after a failed checkout", order_id, exc_info=True
            )
        text, keyboard = _notice(ICON.warning, t("checkout.present_failed"))
        await show_screen(update, context, text=text, keyboard=keyboard)
        return False
    body = (esc(note) + "\n" if note else "") + esc(t("checkout.presented", id=order_id))
    text = screen(ICON.ok, t("checkout.title"), body, step=(2, 3, t("checkout.step_payment")))
    await show_screen(update, context, text=text, keyboard=InlineKeyboardMarkup([nav_row()]))
    return True


async def checkout_pay(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    touch_customer(update)
    user_id = require_user(update).id
    provider = payments.get_provider(context)
    try:
        created = orders_repo.create_order(user_id, provider.mode)
    except orders_repo.OpenOrderExists as exc:
        existing = orders_repo.get(exc.order_id)
        if existing is None:  # vanished between the failure and this read
            await _show_empty(update, context)
        else:
            await show_open_order(update, context, existing)
        return
    except orders_repo.EmptyCartError:
        await _show_empty(update, context)
        return
    except orders_repo.OrderTooLarge as exc:
        await _show_too_large(update, context, exc.total)
        return
    except orders_repo.InsufficientStock as exc:
        text, keyboard = _notice(ICON.warning, t("checkout.stock_short", available=exc.available))
        await show_screen(update, context, text=text, keyboard=keyboard)
        return

    note = tn("checkout.dropped_note", created.dropped_count) if created.dropped_count else ""
    await _present(update, context, created.order_id, note)


# ---------------------------------------------------------------------------
# Resume / cancel
# ---------------------------------------------------------------------------


async def _owned_pending(update: Update, order_id: int) -> sqlite3.Row | None:
    """The caller's own order if it can still be acted on; otherwise answers the
    callback and returns None."""
    order = await get_owned_order(update, order_id)
    if order is None:
        return None
    if order["status"] != "pending_payment":
        await require_query(update).answer(t("checkout.cancel_refused"), show_alert=True)
        return None
    return order


async def checkout_resume(
    update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: int
) -> None:
    order = await _owned_pending(update, order_id)
    if order is None:
        return
    provider = payments.get_provider(context)
    if order["payment_mode"] != provider.mode:
        # Created under a different payment mode: it can't be paid here.
        orders_repo.cancel_order(order_id, "customer")
        text, keyboard = _notice(ICON.warning, t("checkout.mode_changed"))
        await show_screen(update, context, text=text, keyboard=keyboard)
        return
    if orders_repo.is_expired(order):
        await require_query(update).answer(t("pay.err_expired"), show_alert=True)
        return
    await _present(update, context, order_id)


async def checkout_cancel_ask(
    update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: int
) -> None:
    if await _owned_pending(update, order_id) is None:
        return
    keyboard = InlineKeyboardMarkup(
        [
            [
                _button(t("checkout.yes_cancel_button"), cb("co", "cancelyes", order_id)),
                _button(t("checkout.keep_button"), cb("co", "cancelno", order_id)),
            ],
            nav_row(back=cb("co", "cancelno", order_id)),
        ]
    )
    text = screen(ICON.warning, t("checkout.title"), esc(t("checkout.cancel_confirm", id=order_id)))
    await show_screen(update, context, text=text, keyboard=keyboard)


async def checkout_cancel_yes(
    update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: int
) -> None:
    if await _owned_pending(update, order_id) is None:
        return
    try:
        restored = orders_repo.cancel_order(order_id, "customer")
    except orders_repo.IllegalTransitionError:
        await require_query(update).answer(t("checkout.cancel_refused"), show_alert=True)
        return
    key = "checkout.cancelled_restored" if restored else "checkout.cancelled"
    keyboard = InlineKeyboardMarkup(
        [
            [_button(t("catalogue.view_cart_button"), cb("cart", "view"))],
            [_button(t("common.browse_button"), cb("cat", "cats"))],
            nav_row(),
        ]
    )
    text = screen(ICON.ok, t("checkout.title"), esc(t(key, id=order_id)))
    await show_screen(update, context, text=text, keyboard=keyboard)


async def checkout_cancel_no(
    update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: int
) -> None:
    order = await _owned_pending(update, order_id)
    if order is not None:
        await show_open_order(update, context, order)


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
    provider = payments.get_provider(context)

    if provider.mode != MOCK_MODE:  # (1) unreachable outside demo mode
        await query.answer(t("checkout.mock_unavailable"), show_alert=True)
        return
    # Ownership comes before the order-mode check so someone else's order is
    # refused exactly like a nonexistent one.
    order = await get_owned_order(update, order_id)  # (3)
    if order is None:
        return
    if order["payment_mode"] != MOCK_MODE:  # (2)
        await query.answer(t("checkout.mock_unavailable"), show_alert=True)
        return

    rejection = check_payable(order, user_id, order["total_stars"], "XTR", provider.mode)
    if rejection is not None:
        await query.answer(t(REJECTION_MESSAGE_KEY[rejection]), show_alert=True)
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
    text = screen(
        ICON.demo,
        t("checkout.title"),
        esc(t("checkout.mock_done", id=order_id)),
        step=(3, 3, t("checkout.step_delivery")),
    )
    keyboard = InlineKeyboardMarkup(
        [[_button(t("common.my_library_button"), cb("lib", "list", 0))], nav_row()]
    )
    await show_screen(update, context, text=text, keyboard=keyboard)


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
