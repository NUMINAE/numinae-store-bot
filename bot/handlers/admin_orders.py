"""Admin order management: browse/filter/view (the "adm:o:" namespace) and
act — verify, reject, cancel, ship, deliver (the "adm:ord:" namespace,
reusing the callback prefix already baked into the hash-submission owner
notification from Phase 3, so the same buttons work whether the action is
triggered from that notification or from the order card here).

Idempotency is the central concern: every status-changing action re-reads the
order fresh, lets set_status()/reject()/cancel() validate the transition, and
on IllegalTransitionError shows a plain alert with the order's *actual*
current status rather than crashing or double-writing. Every action ends by
re-rendering show_order_card() over whatever message triggered it — the
notification or a previously-open card — which simultaneously satisfies
"re-render my order card" and "edit the notification to show the outcome and
remove its buttons", since the new card's buttons are recomputed from the
order's new status.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from .. import jobs
from ..i18n import t
from ..keyboards import cb, parse_cb_id
from ..render import bold, esc, pre, usd
from ..render import code as render_code
from ..repo import admin_pending as pending_repo
from ..repo import customers as customers_repo
from ..repo import orders as orders_repo
from ..repo import payment_methods as payment_methods_repo
from ..security import owner_only
from ..ui import show_screen
from . import admin_common, shipping
from .common import get_lang

logger = logging.getLogger(__name__)

ORDERS_PAGE_SIZE = 5

_EXPIRED_MENU_ALERT = "This menu has expired."

FILTERS = [
    ("verify", "Needs verification"),
    ("ship_wait", "Awaiting shipping"),
    ("ready", "Ready to ship"),
    ("shipped", "Shipped"),
    ("all", "All orders"),
    ("closed", "Closed"),
]
_FILTER_LABELS = dict(FILTERS)

REJECT_PRESETS = {
    "tx_not_found": "Transaction not found on the blockchain.",
    "amount": "Amount sent does not match the order total.",
    "network": "Sent on the wrong network for the selected payment method.",
}

_ALREADY_PHRASES = {
    "awaiting_payment": "still awaiting payment",
    "awaiting_verification": "awaiting verification",
    "awaiting_shipping": "already verified (awaiting shipping)",
    "confirmed": "already confirmed and ready to ship",
    "shipped": "already shipped",
    "delivered": "already delivered",
    "rejected": "already rejected",
    "cancelled": "already cancelled",
    "expired": "expired",
}


def _already_alert(order_id: int, current_status: str) -> str:
    phrase = _ALREADY_PHRASES.get(
        current_status, orders_repo.STATUS_LABELS.get(current_status, current_status)
    )
    return f"Order #{order_id} is {phrase}."


def _time_ago(created_at: str) -> str:
    try:
        created = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return created_at
    seconds = int((datetime.now(UTC) - created).total_seconds())
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


# ---------------------------------------------------------------------------
# Browse — "adm:o" / "adm:o:l:{filter}:{page}" / "adm:o:v:{id}"
# ---------------------------------------------------------------------------


async def show_orders_root(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    counts = {key: orders_repo.count_by_filter(key) for key, _ in FILTERS}
    buttons = [
        [
            InlineKeyboardButton(
                f"Needs verification ({counts['verify']})",
                callback_data=cb("adm", "o", "l", "verify", 0),
            ),
            InlineKeyboardButton(
                f"Awaiting shipping ({counts['ship_wait']})",
                callback_data=cb("adm", "o", "l", "ship_wait", 0),
            ),
        ],
        [
            InlineKeyboardButton(
                f"Ready to ship ({counts['ready']})", callback_data=cb("adm", "o", "l", "ready", 0)
            ),
            InlineKeyboardButton(
                f"Shipped ({counts['shipped']})", callback_data=cb("adm", "o", "l", "shipped", 0)
            ),
        ],
        [
            InlineKeyboardButton("All orders", callback_data=cb("adm", "o", "l", "all", 0)),
            InlineKeyboardButton(
                f"Closed ({counts['closed']})", callback_data=cb("adm", "o", "l", "closed", 0)
            ),
        ],
        [InlineKeyboardButton("Back to admin", callback_data=cb("adm", "home"))],
    ]
    await show_screen(update, context, text=bold("Orders"), keyboard=InlineKeyboardMarkup(buttons))


async def show_order_list(
    update: Update, context: ContextTypes.DEFAULT_TYPE, filter_key: str, page: int
) -> None:
    total = orders_repo.count_by_filter(filter_key)
    filter_label = _FILTER_LABELS.get(filter_key, filter_key)

    if total == 0:
        text = f"{bold(filter_label)}\n\nNo orders here."
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("Back to filters", callback_data=cb("adm", "o"))]]
        )
        await show_screen(update, context, text=text, keyboard=keyboard)
        return

    total_pages = (total + ORDERS_PAGE_SIZE - 1) // ORDERS_PAGE_SIZE
    page = max(0, min(page, total_pages - 1))
    rows = orders_repo.list_by_filter(filter_key, ORDERS_PAGE_SIZE, page * ORDERS_PAGE_SIZE)

    buttons = []
    for o in rows:
        status_label = orders_repo.STATUS_LABELS.get(o["status"], o["status"])
        label = (
            f"#{o['id']} · {usd(o['total_usd'])} · {status_label} · {_time_ago(o['created_at'])}"
        )
        buttons.append([InlineKeyboardButton(label, callback_data=cb("adm", "o", "v", o["id"]))])

    nav = []
    if page > 0:
        nav.append(
            InlineKeyboardButton("◀", callback_data=cb("adm", "o", "l", filter_key, page - 1))
        )
    nav.append(InlineKeyboardButton(f"Page {page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(
            InlineKeyboardButton("▶", callback_data=cb("adm", "o", "l", filter_key, page + 1))
        )
    buttons.append(nav)
    buttons.append([InlineKeyboardButton("Back to filters", callback_data=cb("adm", "o"))])

    await show_screen(
        update, context, text=bold(filter_label), keyboard=InlineKeyboardMarkup(buttons)
    )


def _order_action_buttons(order) -> list[list[InlineKeyboardButton]]:
    status = order["status"]
    order_id = order["id"]
    if status == "awaiting_payment":
        return [
            [
                InlineKeyboardButton(
                    "Cancel order", callback_data=cb("adm", "ord", "cancel", order_id)
                )
            ]
        ]
    if status == "awaiting_verification":
        return [
            [
                InlineKeyboardButton("Verify", callback_data=cb("adm", "ord", "verify", order_id)),
                InlineKeyboardButton("Reject", callback_data=cb("adm", "ord", "reject", order_id)),
            ],
            [
                InlineKeyboardButton(
                    "Cancel order", callback_data=cb("adm", "ord", "cancel", order_id)
                )
            ],
        ]
    if status == "rejected":
        return [
            [
                InlineKeyboardButton(
                    "Cancel order", callback_data=cb("adm", "ord", "cancel", order_id)
                )
            ]
        ]
    if status == "awaiting_shipping":
        # Deliberately no cancel here: ALLOWED_TRANSITIONS has no path out of
        # awaiting_shipping except confirmed — payment is already verified.
        return [
            [
                InlineKeyboardButton(
                    "Resend address request", callback_data=cb("adm", "ord", "resend", order_id)
                )
            ]
        ]
    if status == "confirmed":
        return [
            [InlineKeyboardButton("Mark shipped", callback_data=cb("adm", "ord", "ship", order_id))]
        ]
    if status == "shipped":
        return [
            [
                InlineKeyboardButton(
                    "Mark delivered", callback_data=cb("adm", "ord", "deliver", order_id)
                )
            ]
        ]
    return []


async def show_order_card(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    order_id: int,
    notice: str | None = None,
    alert: str | None = None,
) -> None:
    order, items = orders_repo.get_with_items(order_id)
    if order is None:
        text = "Order not found."
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("Back to orders", callback_data=cb("adm", "o"))]]
        )
        await show_screen(update, context, text=text, keyboard=keyboard, alert=alert)
        return

    customer = customers_repo.get(order["user_id"])
    pm = payment_methods_repo.get(order["payment_method"])
    status_label = orders_repo.STATUS_LABELS.get(order["status"], order["status"])

    lines = [bold(f"Order #{order_id}"), f"Status: {esc(status_label)}", ""]
    lines.append(f"Created: {esc(order['created_at'])} UTC")
    if order["status"] == "awaiting_payment" and order["expires_at"]:
        lines.append(f"Expires: {esc(order['expires_at'])} UTC")

    lines.append("")
    if customer:
        username_part = f" @{esc(customer['username'])}" if customer["username"] else ""
        first_name = esc(customer["first_name"]) if customer["first_name"] else "(no name)"
        lines.append(f"Customer: {first_name}{username_part} (id {order['user_id']})")
    else:
        lines.append(f"Customer: id {order['user_id']}")

    lines.append("")
    for item in items:
        item_name = f"{item['product_name']} {item['variant_label']}".strip()
        lines.append(
            f"{esc(item_name)} — {item['quantity']} × "
            f"{usd(item['unit_price_usd'])} = {usd(item['line_total_usd'])}"
        )
    lines += ["", bold(f"Total: {usd(order['total_usd'])}")]

    lines.append("")
    lines.append(
        f"Payment: {esc(pm['asset'])} ({esc(pm['network'])})" if pm else "Payment: (unknown method)"
    )

    if order["tx_hash"]:
        if pm and pm["explorer_url"]:
            link = esc(pm["explorer_url"].replace("{tx}", order["tx_hash"]))
            lines.append(f'Hash: {render_code(order["tx_hash"])} — <a href="{link}">view</a>')
        else:
            lines.append(f"Hash: {render_code(order['tx_hash'])}")

    if order["proof_file_id"]:
        lines.append("Payment screenshot: submitted (sent as its own message when received).")

    if order["attempt_count"] and order["attempt_count"] > 1:
        lines.append(f"Attempt count: {order['attempt_count']}")

    if order["reject_reason"]:
        lines += ["", f"Rejection reason: {esc(order['reject_reason'])}"]

    if order["shipping_text"]:
        lines += ["", bold("Shipping address:"), pre(order["shipping_text"])]

    if order["tracking_note"]:
        lines += ["", f"Tracking note: {esc(order['tracking_note'])}"]

    if notice:
        lines += ["", f"⚠️ {esc(notice)}"]

    text = "\n".join(lines)

    buttons = _order_action_buttons(order)
    buttons.append(
        [
            InlineKeyboardButton("Back to list", callback_data=cb("adm", "o")),
            InlineKeyboardButton("Back to admin", callback_data=cb("adm", "home")),
        ]
    )
    await show_screen(
        update, context, text=text, keyboard=InlineKeyboardMarkup(buttons), alert=alert
    )


async def route_browse(
    update: Update, context: ContextTypes.DEFAULT_TYPE, parts: list[str]
) -> None:
    query = update.callback_query
    if len(parts) == 2:
        await show_orders_root(update, context)
        return
    action = parts[2]
    if action == "l":
        if len(parts) < 5:
            await query.answer(_EXPIRED_MENU_ALERT, show_alert=True)
            return
        page = parse_cb_id(parts[4])
        if page is None:
            await query.answer(_EXPIRED_MENU_ALERT, show_alert=True)
            return
        await show_order_list(update, context, parts[3], page)
    elif action == "v":
        if len(parts) < 4:
            await query.answer(_EXPIRED_MENU_ALERT, show_alert=True)
            return
        order_id = parse_cb_id(parts[3])
        if order_id is None:
            await query.answer(_EXPIRED_MENU_ALERT, show_alert=True)
            return
        await show_order_card(update, context, order_id)
    else:
        await query.answer(_EXPIRED_MENU_ALERT, show_alert=True)


# ---------------------------------------------------------------------------
# Find order — /order {id}
# ---------------------------------------------------------------------------


@owner_only
async def find_order(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Usage: /order {id}")
        return
    await show_order_card(update, context, int(args[0]))


# ---------------------------------------------------------------------------
# Manual sweep trigger — /sweep
# ---------------------------------------------------------------------------


@owner_only
async def sweep_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    try:
        summary = await jobs.run_sweep(context)
    except Exception:
        logger.error("Manual /sweep failed", exc_info=True)
        await update.message.reply_text("Sweep failed — check the logs.")
        return

    lines = [
        "Sweep complete.",
        "",
        f"Examined: {summary['examined']}",
        f"Reminders sent: {summary['reminded']}",
        f"Orders expired: {summary['expired']}",
    ]
    if summary["notify_failures"]:
        lines.append(f"Notification failures (logged): {summary['notify_failures']}")
    await update.message.reply_text("\n".join(lines))


# ---------------------------------------------------------------------------
# Customer notifications — each wrapped so a delivery failure never rolls
# back the already-committed status change; the caller surfaces "could not
# notify customer" on the order card instead.
# ---------------------------------------------------------------------------


async def _notify_customer_verified(context: ContextTypes.DEFAULT_TYPE, order_id: int) -> bool:
    """Phase 6: the location is collected before payment, so verification is
    the final customer-facing step — no address prompt follows it anymore."""
    order = orders_repo.get(order_id)
    if order is None:
        return False
    lang = get_lang(order["user_id"])
    order_heading = bold(t("checkout.order_heading", lang, id=order_id))
    text = t("checkout.verified_confirmed", lang, order_heading=order_heading)
    try:
        await context.bot.send_message(chat_id=order["user_id"], text=text)
        return True
    except Exception:
        logger.error(
            "Failed to notify customer #%s about verification for order #%s",
            order["user_id"],
            order_id,
            exc_info=True,
        )
        return False


async def _notify_customer_rejected(
    context: ContextTypes.DEFAULT_TYPE, order_id: int, reason: str
) -> bool:
    order = orders_repo.get(order_id)
    if order is None:
        return False
    lang = get_lang(order["user_id"])
    order_heading = bold(t("checkout.order_heading", lang, id=order_id))
    text = t("checkout.rejected", lang, order_heading=order_heading, reason=esc(reason))
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t("checkout.send_another_hash_button", lang),
                    callback_data=cb("co", "hash", order_id),
                )
            ],
            [
                InlineKeyboardButton(
                    t("checkout.cancel_order_button", lang),
                    callback_data=cb("co", "cancel", order_id),
                )
            ],
        ]
    )
    try:
        await context.bot.send_message(chat_id=order["user_id"], text=text, reply_markup=keyboard)
        return True
    except Exception:
        logger.error(
            "Failed to notify customer #%s about rejection for order #%s",
            order["user_id"],
            order_id,
            exc_info=True,
        )
        return False


async def _notify_customer_cancelled(context: ContextTypes.DEFAULT_TYPE, order_id: int) -> bool:
    order = orders_repo.get(order_id)
    if order is None:
        return False
    lang = get_lang(order["user_id"])
    order_heading = bold(t("checkout.order_heading", lang, id=order_id))
    text = t("checkout.admin_cancelled", lang, order_heading=order_heading)
    try:
        await context.bot.send_message(chat_id=order["user_id"], text=text)
        return True
    except Exception:
        logger.error(
            "Failed to notify customer #%s about cancellation for order #%s",
            order["user_id"],
            order_id,
            exc_info=True,
        )
        return False


async def _notify_customer_shipped(
    context: ContextTypes.DEFAULT_TYPE, order_id: int, note: str | None
) -> bool:
    order = orders_repo.get(order_id)
    if order is None:
        return False
    lang = get_lang(order["user_id"])
    order_heading = bold(t("checkout.order_heading", lang, id=order_id))
    text = t("checkout.shipped", lang, order_heading=order_heading)
    if note:
        text += "\n\n" + t("checkout.tracking_note_line", lang, note=esc(note))
    try:
        await context.bot.send_message(chat_id=order["user_id"], text=text)
        return True
    except Exception:
        logger.error(
            "Failed to notify customer #%s about shipping for order #%s",
            order["user_id"],
            order_id,
            exc_info=True,
        )
        return False


async def _notify_customer_delivered(context: ContextTypes.DEFAULT_TYPE, order_id: int) -> bool:
    order = orders_repo.get(order_id)
    if order is None:
        return False
    lang = get_lang(order["user_id"])
    order_heading = bold(t("checkout.order_heading", lang, id=order_id))
    text = t("checkout.delivered", lang, order_heading=order_heading)
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t("common.browse_teas_button", lang), callback_data=cb("cat", "cats")
                )
            ]
        ]
    )
    try:
        await context.bot.send_message(chat_id=order["user_id"], text=text, reply_markup=keyboard)
        return True
    except Exception:
        logger.error(
            "Failed to notify customer #%s about delivery for order #%s",
            order["user_id"],
            order_id,
            exc_info=True,
        )
        return False


# ---------------------------------------------------------------------------
# Actions — "adm:ord:*"
# ---------------------------------------------------------------------------


async def action_verify(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: int) -> None:
    order = orders_repo.get(order_id)
    if order is None:
        await update.callback_query.answer("Order not found.", show_alert=True)
        return
    try:
        orders_repo.set_status(order_id, "confirmed", actor="owner")
    except orders_repo.IllegalTransitionError:
        current = orders_repo.get(order_id)
        await show_order_card(
            update, context, order_id, alert=_already_alert(order_id, current["status"])
        )
        return

    sent_ok = await _notify_customer_verified(context, order_id)
    notice = None if sent_ok else "could not notify customer"
    await show_order_card(update, context, order_id, notice=notice)


async def action_reject_menu(
    update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: int
) -> None:
    order = orders_repo.get(order_id)
    if order is None:
        await update.callback_query.answer("Order not found.", show_alert=True)
        return
    if order["status"] != "awaiting_verification":
        await show_order_card(
            update, context, order_id, alert=_already_alert(order_id, order["status"])
        )
        return

    text = f"Why is order #{order_id} being rejected?"
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Transaction not found",
                    callback_data=cb("adm", "ord", "rj", "tx_not_found", order_id),
                ),
                InlineKeyboardButton(
                    "Amount incorrect", callback_data=cb("adm", "ord", "rj", "amount", order_id)
                ),
            ],
            [
                InlineKeyboardButton(
                    "Wrong network", callback_data=cb("adm", "ord", "rj", "network", order_id)
                ),
                InlineKeyboardButton(
                    "Other (type a reason)", callback_data=cb("adm", "ord", "rj", "other", order_id)
                ),
            ],
            [InlineKeyboardButton("Back to order", callback_data=cb("adm", "o", "v", order_id))],
        ]
    )
    await show_screen(update, context, text=text, keyboard=keyboard)


async def action_reject_preset(
    update: Update, context: ContextTypes.DEFAULT_TYPE, code: str, order_id: int
) -> None:
    if code == "other":
        pending_repo.set(update.effective_user.id, "order_reject_reason", target_id=order_id)
        await admin_common.prompt(update, context, "Type the rejection reason (1–200 characters).")
        return

    reason = REJECT_PRESETS.get(code)
    if reason is None:
        await update.callback_query.answer("Unknown reason.", show_alert=True)
        return
    await _do_reject(update, context, order_id, reason)


async def apply_reject_reason(update, context, target_id, context_id, raw) -> None:
    ok, value, message = admin_common.validate_short_text(raw, 1, 200)
    if not ok:
        await admin_common.reprompt_after_invalid(update, context, message)
        return
    pending_repo.clear(update.effective_user.id)
    await update.message.reply_text(f"Rejecting order #{target_id}: {esc(value)}")
    await _do_reject(update, context, target_id, value)


async def _do_reject(
    update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: int, reason: str
) -> None:
    order = orders_repo.get(order_id)
    if order is None:
        if update.callback_query:
            await update.callback_query.answer("Order not found.", show_alert=True)
        return
    try:
        orders_repo.reject(order_id, actor="owner", reason=reason)
    except orders_repo.IllegalTransitionError:
        current = orders_repo.get(order_id)
        await show_order_card(
            update, context, order_id, alert=_already_alert(order_id, current["status"])
        )
        return

    sent_ok = await _notify_customer_rejected(context, order_id, reason)
    notice = None if sent_ok else "could not notify customer"
    await show_order_card(update, context, order_id, notice=notice)


async def action_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: int) -> None:
    order = orders_repo.get(order_id)
    if order is None:
        await update.callback_query.answer("Order not found.", show_alert=True)
        return
    try:
        orders_repo.cancel(order_id, actor="owner")
    except orders_repo.IllegalTransitionError:
        current = orders_repo.get(order_id)
        await show_order_card(
            update, context, order_id, alert=_already_alert(order_id, current["status"])
        )
        return

    sent_ok = await _notify_customer_cancelled(context, order_id)
    notice = None if sent_ok else "could not notify customer"
    await show_order_card(update, context, order_id, notice=notice)


async def action_ship_menu(
    update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: int
) -> None:
    order = orders_repo.get(order_id)
    if order is None:
        await update.callback_query.answer("Order not found.", show_alert=True)
        return
    if order["status"] != "confirmed":
        await show_order_card(
            update, context, order_id, alert=_already_alert(order_id, order["status"])
        )
        return

    text = f"Mark order #{order_id} as shipped?"
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Add tracking note", callback_data=cb("adm", "ord", "shipnote", order_id)
                )
            ],
            [
                InlineKeyboardButton(
                    "Ship without note", callback_data=cb("adm", "ord", "shipnow", order_id)
                )
            ],
            [InlineKeyboardButton("Back to order", callback_data=cb("adm", "o", "v", order_id))],
        ]
    )
    await show_screen(update, context, text=text, keyboard=keyboard)


async def action_ship_note_prompt(
    update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: int
) -> None:
    pending_repo.set(update.effective_user.id, "order_tracking_note", target_id=order_id)
    await admin_common.prompt(update, context, "Type the tracking note (1–200 characters).")


async def action_ship_now(
    update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: int
) -> None:
    await _do_ship(update, context, order_id, note=None)


async def apply_tracking_note(update, context, target_id, context_id, raw) -> None:
    ok, value, message = admin_common.validate_short_text(raw, 1, 200)
    if not ok:
        await admin_common.reprompt_after_invalid(update, context, message)
        return
    pending_repo.clear(update.effective_user.id)
    await _do_ship(update, context, target_id, note=value)


async def _do_ship(
    update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: int, note: str | None
) -> None:
    order = orders_repo.get(order_id)
    if order is None:
        if update.callback_query:
            await update.callback_query.answer("Order not found.", show_alert=True)
        return
    try:
        if note:
            orders_repo.set_tracking_note(order_id, note)
        orders_repo.set_status(order_id, "shipped", actor="owner")
    except orders_repo.IllegalTransitionError:
        current = orders_repo.get(order_id)
        await show_order_card(
            update, context, order_id, alert=_already_alert(order_id, current["status"])
        )
        return

    sent_ok = await _notify_customer_shipped(context, order_id, note)
    notice = None if sent_ok else "could not notify customer"
    await show_order_card(update, context, order_id, notice=notice)


async def action_deliver(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: int) -> None:
    order = orders_repo.get(order_id)
    if order is None:
        await update.callback_query.answer("Order not found.", show_alert=True)
        return
    try:
        orders_repo.set_status(order_id, "delivered", actor="owner")
    except orders_repo.IllegalTransitionError:
        current = orders_repo.get(order_id)
        await show_order_card(
            update, context, order_id, alert=_already_alert(order_id, current["status"])
        )
        return

    sent_ok = await _notify_customer_delivered(context, order_id)
    notice = None if sent_ok else "could not notify customer"
    await show_order_card(update, context, order_id, notice=notice)


async def action_resend(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: int) -> None:
    order = orders_repo.get(order_id)
    if order is None:
        await update.callback_query.answer("Order not found.", show_alert=True)
        return
    if order["status"] != "awaiting_shipping":
        await show_order_card(
            update, context, order_id, alert=_already_alert(order_id, order["status"])
        )
        return
    sent_ok = await shipping.send_address_prompt(context, order_id)
    notice = None if sent_ok else "could not notify customer"
    await show_order_card(update, context, order_id, notice=notice)


async def route_actions(
    update: Update, context: ContextTypes.DEFAULT_TYPE, parts: list[str]
) -> None:
    query = update.callback_query
    if len(parts) < 3:
        await query.answer(_EXPIRED_MENU_ALERT, show_alert=True)
        return
    action = parts[2]

    if action == "rj":
        if len(parts) < 5:
            await query.answer(_EXPIRED_MENU_ALERT, show_alert=True)
            return
        order_id = parse_cb_id(parts[4])
        if order_id is None:
            await query.answer(_EXPIRED_MENU_ALERT, show_alert=True)
            return
        await action_reject_preset(update, context, parts[3], order_id)
        return

    if len(parts) < 4:
        await query.answer(_EXPIRED_MENU_ALERT, show_alert=True)
        return
    order_id = parse_cb_id(parts[3])
    if order_id is None:
        await query.answer(_EXPIRED_MENU_ALERT, show_alert=True)
        return

    if action == "verify":
        await action_verify(update, context, order_id)
    elif action == "reject":
        await action_reject_menu(update, context, order_id)
    elif action == "cancel":
        await action_cancel(update, context, order_id)
    elif action == "ship":
        await action_ship_menu(update, context, order_id)
    elif action == "shipnote":
        await action_ship_note_prompt(update, context, order_id)
    elif action == "shipnow":
        await action_ship_now(update, context, order_id)
    elif action == "deliver":
        await action_deliver(update, context, order_id)
    elif action == "resend":
        await action_resend(update, context, order_id)
    else:
        await query.answer(_EXPIRED_MENU_ALERT, show_alert=True)
