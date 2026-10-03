"""The recurring sweep and the owner's /sweep command.

One repeating job (every 60 seconds, first run after 30) rather than a job per
order: each pass
  1. expires orders past their reservation window, releasing their keys, and
     tells each customer once;
  2. retries delivery for every paid-but-undelivered order;
  3. alerts the owner once about orders paid more than 30 minutes ago that are
     still undelivered (an in-memory set; a restart may repeat an alert).
Every per-order step is isolated, and the whole pass is wrapped so one bug
never kills the recurring job."""

from __future__ import annotations

import logging
from typing import NamedTuple

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from . import delivery
from .handlers.common import get_lang
from .handlers.payments import alert_owner, safe_send
from .i18n import t
from .keyboards import cb
from .render import bold
from .repo import orders as orders_repo
from .security import owner_only
from .tg import require_message

logger = logging.getLogger(__name__)

SWEEP_INTERVAL_SECONDS = 60
SWEEP_FIRST_RUN_DELAY_SECONDS = 30
OWNER_ALERT_AFTER_MINUTES = 30

# Order ids the owner has already been alerted about (see module docstring).
_alerted: set[int] = set()


class SweepReport(NamedTuple):
    examined: int
    expired: int
    delivered: int  # orders that finished delivering this pass
    failed: int  # orders with at least one item still undelivered


async def _notify_expired(bot: Bot, order_id: int, user_id: int) -> None:
    lang = get_lang(user_id)
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(t("common.browse_button", lang), callback_data=cb("cat", "cats"))]]
    )
    await safe_send(bot, user_id, t("jobs.order_expired", lang, id=order_id), reply_markup=keyboard)


async def run_sweep(bot: Bot) -> SweepReport:
    expired = 0
    delivered = 0
    failed = 0

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
    for order in paid:
        order_id = order["id"]
        try:
            result = await delivery.deliver_order(bot, order_id)
            if result.failed:
                failed += 1
            elif result.delivered:
                delivered += 1
        except Exception:
            logger.error("Sweep: delivery retry failed (order_id=%s)", order_id, exc_info=True)
            failed += 1
        try:
            await _alert_if_stuck(bot, order_id)
        except Exception:
            logger.error("Sweep: stuck-order alert failed (order_id=%s)", order_id, exc_info=True)

    return SweepReport(len(due) + len(paid), expired, delivered, failed)


async def _alert_if_stuck(bot: Bot, order_id: int) -> None:
    order = orders_repo.get(order_id)
    if order is None or order["status"] != "paid" or order_id in _alerted:
        return
    if not orders_repo.paid_longer_than(order, OWNER_ALERT_AFTER_MINUTES):
        return
    _alerted.add(order_id)
    await alert_owner(
        bot,
        f"🚨 {bold(f'Order #{order_id} is paid but still undelivered')}\n"
        f"Paid more than {OWNER_ALERT_AFTER_MINUTES} minutes ago. Buyer id: {order['user_id']}.\n"
        "Check the delivery content or fix and re-run /sweep.",
    )


async def sweep_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """The recurring job. Never raises: a crash here would stop the schedule."""
    try:
        await run_sweep(context.bot)
    except Exception:
        logger.error("Sweep pass crashed", exc_info=True)


@owner_only
async def sweep_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/sweep (owner only): run one pass now and report the counts."""
    message = require_message(update)
    report = await run_sweep(context.bot)
    await message.reply_text(
        f"Sweep done: examined {report.examined}, expired {report.expired}, "
        f"delivered {report.delivered}, failed {report.failed}."
    )
