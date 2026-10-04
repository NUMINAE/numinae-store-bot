"""The recurring sweep and the owner's /sweep command.

One repeating job (every 60 seconds, first run after 30) rather than a job per
order: each pass
  1. expires orders past their reservation window, releasing their keys, and
     tells each customer once;
  2. retries delivery for every paid-but-undelivered order, backing off in
     memory after each failure (1, 2, 4, 8 ... minutes, capped at 60) so a
     permanently broken order is not hammered and logs one error per attempt;
  3. alerts the owner once about orders paid more than 30 minutes ago that are
     still undelivered (an in-memory set; a restart may repeat an alert).
Every per-order step is isolated, and the whole pass is wrapped so one bug
never kills the recurring job."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import NamedTuple

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from . import delivery, health
from .handlers.payments import alert_owner, safe_send
from .i18n import t
from .keyboards import cb
from .render import bold
from .repo import orders as orders_repo
from .security import owner_only
from .tg import require_message
from .ui_kit import ICON

logger = logging.getLogger(__name__)

SWEEP_INTERVAL_SECONDS = 60
SWEEP_FIRST_RUN_DELAY_SECONDS = 30
OWNER_ALERT_AFTER_MINUTES = 30

# Order ids the owner has already been alerted about (see module docstring).
_alerted: set[int] = set()

BACKOFF_CAP_MINUTES = 60

# In-memory delivery-retry backoff per order id. A restart forgets it, which
# only means one earlier retry. _now is swappable so tests can inject a clock.
_now: Callable[[], float] = time.monotonic
_retry: dict[int, _Retry] = {}


@dataclass
class _Retry:
    failures: int
    next_at: float


def backoff_minutes(failures: int) -> int:
    """Wait after the Nth consecutive failure: 1, 2, 4, 8, 16, 32, then 60."""
    return min(2 ** max(failures - 1, 0), BACKOFF_CAP_MINUTES)


def _record_failure(order_id: int) -> _Retry:
    state = _retry.get(order_id)
    failures = (state.failures if state else 0) + 1
    state = _Retry(failures, _now() + backoff_minutes(failures) * 60)
    _retry[order_id] = state
    return state


def _backing_off(order_id: int) -> bool:
    state = _retry.get(order_id)
    return state is not None and _now() < state.next_at


class SweepReport(NamedTuple):
    examined: int
    expired: int
    delivered: int  # orders that finished delivering this pass
    failed: int  # orders with at least one item still undelivered
    skipped: int = 0  # paid orders left alone because they are backing off


async def _notify_expired(bot: Bot, order_id: int, user_id: int) -> None:
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(t("common.browse_button"), callback_data=cb("cat", "cats"))]]
    )
    await safe_send(bot, user_id, t("jobs.order_expired", id=order_id), reply_markup=keyboard)


async def _attempt_delivery(bot: Bot, order_id: int) -> delivery.DeliveryResult | None:
    """One delivery attempt with backoff bookkeeping. Returns None if it crashed."""
    try:
        result = await delivery.deliver_order(bot, order_id)
    except Exception:
        state = _record_failure(order_id)
        logger.error(
            "Sweep: delivery attempt crashed (order_id=%s, attempt=%s, next retry in %s min)",
            order_id,
            state.failures,
            backoff_minutes(state.failures),
            exc_info=True,
        )
        return None
    if result.failed:
        _record_failure(order_id)  # deliver_order already logged each failed item
    elif result.delivered:
        _retry.pop(order_id, None)
    return result


async def retry_delivery_now(bot: Bot, order_id: int) -> delivery.DeliveryResult:
    """The admin's "Retry delivery": ignore and reset any backoff, try now. A
    failure starts the backoff afresh (next automatic retry in a minute)."""
    _retry.pop(order_id, None)
    result = await _attempt_delivery(bot, order_id)
    if result is None:
        return delivery.DeliveryResult([], [])
    return result


async def run_sweep(bot: Bot, *, force: bool = False) -> SweepReport:
    expired = 0
    delivered = 0
    failed = 0
    skipped = 0

    # 1. expiry
    try:
        due = orders_repo.expire_due_orders()
    except Exception:
        logger.error("Sweep: expiry pass failed", exc_info=True)
        due = []
    for order_id, user_id in due:
        expired += 1
        try:
            await _notify_expired(bot, order_id, user_id)
        except Exception:
            logger.error("Sweep: expiry notice failed (order_id=%s)", order_id, exc_info=True)

    # 2. delivery retries, 3. owner alerts for stuck deliveries
    try:
        paid = orders_repo.list_paid_undelivered_orders()
    except Exception:
        logger.error("Sweep: could not list paid orders", exc_info=True)
        paid = []
    for stale in set(_retry) - {order["id"] for order in paid}:
        del _retry[stale]  # delivered, refunded or gone: forget its backoff
    for order in paid:
        order_id = order["id"]
        if not force and _backing_off(order_id):
            skipped += 1
        else:
            result = await _attempt_delivery(bot, order_id)
            if result is None or result.failed:
                failed += 1
            elif result.delivered:
                delivered += 1
        try:
            await _alert_if_stuck(bot, order_id)
        except Exception:
            logger.error("Sweep: stuck-order alert failed (order_id=%s)", order_id, exc_info=True)

    return SweepReport(len(due) + len(paid) - skipped, expired, delivered, failed, skipped)


async def _alert_if_stuck(bot: Bot, order_id: int) -> None:
    order = orders_repo.get(order_id)
    if order is None or order["status"] != "paid" or order_id in _alerted:
        return
    if not orders_repo.paid_longer_than(order, OWNER_ALERT_AFTER_MINUTES):
        return
    _alerted.add(order_id)
    await alert_owner(
        bot,
        f"{ICON.alert} {bold(f'Order #{order_id} is paid but still undelivered')}\n"
        f"Paid more than {OWNER_ALERT_AFTER_MINUTES} minutes ago. Buyer id: {order['user_id']}.\n"
        "Check the delivery content or fix and re-run /sweep.",
    )


async def sweep_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """The recurring job. Never raises: a crash here would stop the schedule."""
    # First thing, in its own guard: even a pass that fails proves the loop is alive.
    try:
        health.touch_heartbeat()
    except Exception:
        logger.warning("Heartbeat update failed", exc_info=True)
    try:
        report = await run_sweep(context.bot)
        logger.info(
            "Sweep: examined=%s expired=%s delivered=%s failed=%s skipped=%s",
            report.examined,
            report.expired,
            report.delivered,
            report.failed,
            report.skipped,
        )
    except Exception:
        logger.error("Sweep pass crashed", exc_info=True)


@owner_only
async def sweep_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/sweep (owner only): run one pass now and report the counts."""
    message = require_message(update)
    report = await run_sweep(context.bot, force=True)  # the owner asked: skip the backoff
    await message.reply_text(
        f"Sweep done: examined {report.examined}, expired {report.expired}, "
        f"delivered {report.delivered}, failed {report.failed}."
    )
