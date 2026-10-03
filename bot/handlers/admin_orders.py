"""Order management for the owner: browse by filter, the order card, and the
actions on it (cancel, retry delivery, resend, refund). Registered under the
"adm:o" (browse) and "adm:ord" (actions) callbacks, plus /order <id>. Admin
screens are English; the notifications sent to the buyer are localized.

Idempotency: every action re-reads the order inside the handler. If the order is
no longer in a state the action applies to (a double tap, a stale card), the
owner is told its REAL current status and the card is re-rendered — nothing is
attempted."""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from .. import delivery, jobs
from ..i18n import t
from ..keyboards import cb, parse_cb_id
from ..refunds import RefundStatus, refund_order
from ..render import bold, code, esc, stars
from ..repo import orders as orders_repo
from ..security import owner_only
from ..tg import require_message, require_query, require_user
from ..ui import show_screen
from .common import get_lang
from .payments import alert_owner, safe_send

logger = logging.getLogger(__name__)

PAGE_SIZE = 5

FILTER_LABELS = {
    "attention": "Needs attention",
    "all": "All",
    "pending_payment": "Pending payment",
    "paid": "Paid",
    "delivered": "Delivered",
    "refunded": "Refunded",
    "closed": "Closed",
}
STATUS_WORDS = {
    "pending_payment": "Pending payment",
    "paid": "Paid",
    "delivered": "Delivered",
    "cancelled": "Cancelled",
    "expired": "Expired",
    "refunded": "Refunded",
}


def _button(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=data)


def age(created_at: str) -> str:
    """A compact age for a UTC datetime text: 5m, 3h, 2d."""
    try:
        then = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return "?"
    seconds = max(0, int((datetime.now(UTC) - then).total_seconds()))
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


# ---------------------------------------------------------------------------
# Browsing
# ---------------------------------------------------------------------------


async def show_root(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    counts = orders_repo.counts_by_filter()
    rows = []
    for key in FILTER_LABELS:
        label = f"{FILTER_LABELS[key]} ({counts[key]})"
        rows.append([_button(label, cb("adm", "o", "l", key, 0))])
    rows.append([_button("Back to admin", cb("adm", "home"))])
    await show_screen(update, context, text=bold("Orders"), keyboard=InlineKeyboardMarkup(rows))


async def show_list(
    update: Update, context: ContextTypes.DEFAULT_TYPE, filter_key: str, page: int
) -> None:
    if filter_key not in orders_repo.ADMIN_FILTERS:
        await show_root(update, context)
        return
    total = orders_repo.count_for_admin(filter_key)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    orders = orders_repo.list_for_admin(filter_key, PAGE_SIZE, page * PAGE_SIZE)

    rows: list[list[InlineKeyboardButton]] = []
    for order in orders:
        label = (
            f"#{order['id']} - {stars(order['total_stars'])} - "
            f"{STATUS_WORDS[order['status']]} - {age(order['created_at'])}"
        )
        rows.append([_button(label, cb("adm", "o", "v", order["id"]))])
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(_button("◀", cb("adm", "o", "l", filter_key, page - 1)))
    if total_pages > 1:
        nav.append(_button(f"Page {page + 1}/{total_pages}", "noop"))
    if page < total_pages - 1:
        nav.append(_button("▶", cb("adm", "o", "l", filter_key, page + 1)))
    if nav:
        rows.append(nav)
    rows.append([_button("Back to orders", cb("adm", "o"))])

    heading = bold(f"{FILTER_LABELS[filter_key]} orders")
    text = heading if orders else f"{heading}\n\nNo orders here."
    await show_screen(update, context, text=text, keyboard=InlineKeyboardMarkup(rows))


# ---------------------------------------------------------------------------
# The card
# ---------------------------------------------------------------------------


def _buyer_line(buyer: sqlite3.Row | None, user_id: int) -> str:
    name = esc(buyer["first_name"]) if buyer is not None and buyer["first_name"] else "(no name)"
    username = f" @{esc(buyer['username'])}" if buyer is not None and buyer["username"] else ""
    return f"Buyer: {name}{username} ({code(user_id)})"


def render_card(
    card: orders_repo.AdminCard, notice: str | None = None
) -> tuple[str, InlineKeyboardMarkup]:
    order = card.order
    order_id = order["id"]
    lines: list[str] = []
    if notice:
        lines += [f"ℹ️ {esc(notice)}", ""]
    lines.append(f"{bold(f'Order #{order_id}')} — {STATUS_WORDS[order['status']]}")
    lines.append(_buyer_line(card.buyer, order["user_id"]))
    for label, column in (
        ("Created", "created_at"),
        ("Paid", "paid_at"),
        ("Delivered", "delivered_at"),
        ("Refunded", "refunded_at"),
        ("Expires", "expires_at"),
    ):
        if order[column]:
            lines.append(f"{label}: {esc(order[column])} UTC")
    lines.append(f"Total: {stars(order['total_stars'])} · Mode: {esc(order['payment_mode'])}")
    if order["telegram_payment_charge_id"]:
        lines.append(f"Charge id: {code(order['telegram_payment_charge_id'])}")

    lines += ["", bold("Items")]
    for item in card.items:
        name = esc(f"{item['product_name']} — {item['variant_label']}")
        state = (
            f"delivered {esc(item['delivered_at'])}" if item["delivered_at"] else "not delivered"
        )
        lines.append(
            f"• {name} × {item['quantity']} — {stars(item['line_total_stars'])} — "
            f"{esc(item['delivery_kind'])}, {state}"
        )
    lines += ["", bold("Recent events")]
    for event in card.events:
        arrow = (
            f"{esc(event['from_status'] or 'new')} → {esc(event['to_status'])}"
            if event["from_status"] != event["to_status"]
            else f"note ({esc(event['to_status'])})"
        )
        note = f" — {esc(event['note'])}" if event["note"] else ""
        lines.append(f"{esc(event['created_at'])} {arrow} [{esc(event['actor'])}]{note}")

    status = order["status"]
    rows: list[list[InlineKeyboardButton]] = []
    if status == "pending_payment":
        rows.append([_button("Cancel order", cb("adm", "ord", "cancel", order_id))])
    elif status == "paid":
        rows.append(
            [
                _button("Retry delivery", cb("adm", "ord", "retry", order_id)),
                _button("Refund", cb("adm", "ord", "refund", order_id)),
            ]
        )
    elif status == "delivered":
        rows.append(
            [
                _button("Resend to buyer", cb("adm", "ord", "resend", order_id)),
                _button("Refund", cb("adm", "ord", "refund", order_id)),
            ]
        )
    rows.append([_button("Back to orders", cb("adm", "o"))])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def show_card(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    order_id: int,
    notice: str | None = None,
    alert: str | None = None,
) -> None:
    card = orders_repo.get_admin_card(order_id)
    if card is None:
        query = update.callback_query
        if query is not None:
            await query.answer("Order not found.", show_alert=True)
        else:
            await require_message(update).reply_text("Order not found.")
        return
    text, keyboard = render_card(card, notice)
    await show_screen(update, context, text=text, keyboard=keyboard, alert=alert)


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


async def _wrong_state(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: int) -> None:
    """The action no longer applies: say what the order REALLY is, re-render."""
    order = orders_repo.get(order_id)
    status = STATUS_WORDS.get(order["status"], order["status"]) if order else "missing"
    await show_card(update, context, order_id, alert=f"Order #{order_id} is now: {status}.")


def _confirm_screen(text: str, yes: str, back: str) -> tuple[str, InlineKeyboardMarkup]:
    return text, InlineKeyboardMarkup([[_button("Confirm", yes), _button("Cancel", back)]])


async def _notify_buyer(
    context: ContextTypes.DEFAULT_TYPE, user_id: int, key: str, **kwargs: object
) -> bool:
    return await safe_send(context.bot, user_id, t(key, get_lang(user_id), **kwargs))


async def action_cancel(
    update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: int, confirmed: bool
) -> None:
    order = orders_repo.get(order_id)
    if order is None or order["status"] != "pending_payment":
        await _wrong_state(update, context, order_id)
        return
    if not confirmed:
        text, keyboard = _confirm_screen(
            f"Cancel order #{order_id}? Its reserved keys are released. The buyer's cart is NOT "
            "restored, and the buyer is told.",
            cb("adm", "ord", "cancel", order_id, "yes"),
            cb("adm", "o", "v", order_id),
        )
        await show_screen(update, context, text=text, keyboard=keyboard)
        return
    try:
        orders_repo.cancel_order(order_id, "owner")
    except orders_repo.IllegalTransitionError:
        await _wrong_state(update, context, order_id)
        return
    notified = await _notify_buyer(context, order["user_id"], "owner.order_cancelled", id=order_id)
    notice = "Order cancelled." + ("" if notified else " Could not notify customer.")
    await show_card(update, context, order_id, notice=notice)


async def action_retry(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: int) -> None:
    order = orders_repo.get(order_id)
    if order is None or order["status"] != "paid":
        await _wrong_state(update, context, order_id)
        return
    result = await jobs.retry_delivery_now(context.bot, order_id)
    if result.failed:
        notice = (
            f"Delivered {len(result.delivered)} item(s); {len(result.failed)} still failing "
            "(see the logs). It will keep retrying."
        )
    elif result.delivered:
        notice = f"Delivered {len(result.delivered)} item(s)."
    else:
        notice = "Nothing was sent (a delivery may already be in progress)."
    await show_card(update, context, order_id, notice=notice)


async def action_resend(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: int) -> None:
    card = orders_repo.get_admin_card(order_id)
    if card is None or card.order["status"] != "delivered":
        await _wrong_state(update, context, order_id)
        return
    sent = 0
    for item in card.items:
        if await delivery.deliver_item(context.bot, item["id"]):
            sent += 1
    notice = f"Re-sent {sent} of {len(card.items)} item(s) to the buyer."
    await show_card(update, context, order_id, notice=notice)


async def action_refund(
    update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: int, confirmed: bool
) -> None:
    order = orders_repo.get(order_id)
    if order is not None and order["status"] == "refunded":
        await show_card(update, context, order_id, alert="Already refunded.")
        return
    if order is None or order["status"] not in ("paid", "delivered"):
        await _wrong_state(update, context, order_id)
        return
    if not confirmed:
        text, keyboard = _confirm_screen(
            f"Refund order #{order_id}? The full {stars(order['total_stars'])} goes back to the "
            "buyer. Sold licence keys will be revoked. Delivered text and files cannot be "
            "unsent. The buyer is told.",
            cb("adm", "ord", "refund", order_id, "yes"),
            cb("adm", "o", "v", order_id),
        )
        await show_screen(update, context, text=text, keyboard=keyboard)
        return

    result = await refund_order(context.bot, order_id, "owner")
    status = result.status
    if status is RefundStatus.REFUNDED:
        notified = await _notify_buyer(
            context,
            order["user_id"],
            "owner.order_refunded",
            id=order_id,
            total=stars(order["total_stars"]),
        )
        notice = f"Refunded: {result.detail}." + ("" if notified else " Could not notify customer.")
        await show_card(update, context, order_id, notice=notice)
    elif status is RefundStatus.DB_FAILED_AFTER_REFUND:
        charge = order["telegram_payment_charge_id"] or "(none)"
        await alert_owner(
            context.bot,
            f"🚨 {bold('Refund sent but the order was NOT updated')}\nOrder: #{order_id}\n"
            f"Charge id: {code(charge)}\nMoney was refunded, status was not updated, "
            "reconcile manually.",
        )
        await show_card(
            update,
            context,
            order_id,
            notice="Money was refunded but the status was not updated. Reconcile manually.",
        )
    elif status is RefundStatus.PROVIDER_FAILED:
        await show_card(update, context, order_id, notice=f"Refund failed: {result.detail}")
    elif status is RefundStatus.ALREADY_REFUNDED:
        await show_card(update, context, order_id, notice="Already refunded.")
    elif status is RefundStatus.IN_FLIGHT:
        await show_card(
            update, context, order_id, notice="A refund for this order is already in progress."
        )
    elif status is RefundStatus.NO_CHARGE_ID:
        await show_card(
            update,
            context,
            order_id,
            notice="No payment charge id is stored, so Telegram can't refund it. Refund by hand.",
        )
    else:  # NOT_FOUND / NOT_REFUNDABLE
        await _wrong_state(update, context, order_id)


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------


async def route_browse(
    update: Update, context: ContextTypes.DEFAULT_TYPE, parts: list[str]
) -> None:
    """adm:o | adm:o:l:{filter}:{page} | adm:o:v:{id}"""
    query = require_query(update)
    if len(parts) == 2:
        await show_root(update, context)
    elif parts[2] == "l" and len(parts) == 5:
        page = parse_cb_id(parts[4])
        if page is None:
            await query.answer("This menu has expired.", show_alert=True)
            return
        await show_list(update, context, parts[3], page)
    elif parts[2] == "v" and len(parts) == 4:
        order_id = parse_cb_id(parts[3])
        if order_id is None:
            await query.answer("This menu has expired.", show_alert=True)
            return
        await show_card(update, context, order_id)
    else:
        await query.answer("This menu has expired.", show_alert=True)


async def route_actions(
    update: Update, context: ContextTypes.DEFAULT_TYPE, parts: list[str]
) -> None:
    """adm:ord:{action}:{id}[:yes]"""
    query = require_query(update)
    order_id = parse_cb_id(parts[3]) if len(parts) in (4, 5) else None
    confirmed = len(parts) == 5 and parts[4] == "yes"
    if order_id is None or (len(parts) == 5 and not confirmed):
        await query.answer("This menu has expired.", show_alert=True)
        return
    action = parts[2]
    if action == "cancel":
        await action_cancel(update, context, order_id, confirmed)
    elif action == "refund":
        await action_refund(update, context, order_id, confirmed)
    elif action == "retry" and not confirmed:
        await action_retry(update, context, order_id)
    elif action == "resend" and not confirmed:
        await action_resend(update, context, order_id)
    else:
        await query.answer("This menu has expired.", show_alert=True)


@owner_only
async def find_order(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/order <id>: open an order's card. The id goes through the same bounded
    integer parser as callback ids, so junk, negatives and huge values get the
    usage message and never raise."""
    message = require_message(update)
    require_user(update)
    args = context.args or []
    order_id = parse_cb_id(args[0]) if len(args) == 1 else None
    if order_id is None:
        await message.reply_text("Usage: /order <order id>  (a whole number, e.g. /order 42)")
        return
    if orders_repo.get_admin_card(order_id) is None:
        await message.reply_text(f"No order #{order_id}.")
        return
    await show_card(update, context, order_id)
