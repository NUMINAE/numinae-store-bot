"""The payment reminder / expiry sweep. One JobQueue job, run every 30
minutes, sweeping all awaiting_payment orders in a single pass — not a job
per order. That makes the sweep idempotent and self-healing after downtime:
there's no per-order state to rebuild on restart, and an order that blew
through several thresholds while the bot was down just gets caught up to the
right threshold on the next pass (see _thresholds_passed / run_sweep below —
reminders_sent is *set* to the threshold count, never incremented).

Both the recurring job (sweep_job) and the manual /sweep command (in
handlers/admin_orders.py) call run_sweep() and wrap it in their own
try/except, so a bug in one pass never kills the job for the process
lifetime, and a failed manual run reports cleanly instead of crashing.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from .i18n import DEFAULT_LANGUAGE, LANGUAGES, t
from .keyboards import cb
from .render import bold, esc, usd
from .render import code as render_code
from .repo import customers as customers_repo
from .repo import orders as orders_repo
from .repo import settings as settings_repo

logger = logging.getLogger(__name__)

SWEEP_INTERVAL_SECONDS = 30 * 60
SWEEP_FIRST_RUN_DELAY_SECONDS = 30

DEFAULT_REMINDER_PERCENTS = "25,50,83"
RATE_LIMIT_BATCH_THRESHOLD = 20
RATE_LIMIT_SLEEP_SECONDS = 0.1


def _parse_utc(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)


def _elapsed_fraction(order) -> float:
    created = _parse_utc(order["created_at"])
    expires = _parse_utc(order["expires_at"])
    total_seconds = (expires - created).total_seconds()
    if total_seconds <= 0:
        return 1.0  # malformed/zero-width window: treat as already expired
    now = datetime.now(UTC)
    return (now - created).total_seconds() / total_seconds


def _current_percents() -> list[int]:
    raw = (
        settings_repo.get("reminder_percents", DEFAULT_REMINDER_PERCENTS)
        or DEFAULT_REMINDER_PERCENTS
    )
    try:
        return sorted(int(p.strip()) for p in raw.split(","))
    except ValueError:
        return sorted(int(p) for p in DEFAULT_REMINDER_PERCENTS.split(","))


def _thresholds_passed(elapsed_fraction: float, percents: list[int]) -> int:
    return sum(1 for p in percents if elapsed_fraction >= p / 100.0)


def _lang_for(order) -> str:
    """Resolved once per order processed, not re-queried per line rendered.
    A small local helper rather than importing handlers.common: jobs.py is a
    top-level module, and handlers/ already imports from it (admin_orders.py
    calls into here) — importing back from handlers/ would invert that."""
    customer = customers_repo.get(order["user_id"])
    lang = customer["language"] if customer else None
    return lang if lang in LANGUAGES else DEFAULT_LANGUAGE


def _time_left_phrase(order, lang: str) -> str:
    expires = _parse_utc(order["expires_at"])
    remaining_seconds = max(0.0, (expires - datetime.now(UTC)).total_seconds())
    hours = round(remaining_seconds / 3600)
    if hours <= 0:
        return t("jobs.time_left_less_than_hour", lang)
    hour_word = t("jobs.hour_singular", lang) if hours == 1 else t("jobs.hour_plural", lang)
    return t("jobs.time_left_hours", lang, hours=hours, hour_word=hour_word)


def _reminder_tone(threshold_count: int, total_thresholds: int, lang: str) -> str:
    if threshold_count >= total_thresholds:
        return t("jobs.reminder_final", lang)
    if threshold_count == 1:
        return t("jobs.reminder_first", lang)
    return t("jobs.reminder_again", lang)


def _reminder_content(
    order, threshold_count: int, percents: list[int]
) -> tuple[str, InlineKeyboardMarkup]:
    lang = _lang_for(order)
    order_id = order["id"]
    lines = [
        bold(t("checkout.order_heading", lang, id=order_id)),
        _reminder_tone(threshold_count, len(percents), lang),
    ]
    lines += [
        "",
        bold(t("checkout.total", lang, amount=usd(order["total_usd"]))),
        t("checkout.pay_amount", lang, asset=esc(order["pm_asset"])),
        "",
        bold(t("checkout.network_label", lang, network=esc(order["pm_network"]))),
        "",
        render_code(order["wallet_address"]),
    ]
    if order["memo_tag"]:
        lines += [
            "",
            render_code(order["memo_tag"]),
            t("checkout.memo_warning", lang),
        ]
    phrase = _time_left_phrase(order, lang)
    lines += ["", f"{phrase[0].upper()}{phrase[1:]}."]
    text = "\n".join(lines)

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t("checkout.hash_button", lang), callback_data=cb("co", "hash", order_id)
                )
            ],
            [
                InlineKeyboardButton(
                    t("checkout.cancel_order_button", lang),
                    callback_data=cb("co", "cancel", order_id),
                )
            ],
            [
                InlineKeyboardButton(
                    t("common.back_to_menu_button", lang), callback_data=cb("menu", "show")
                )
            ],
        ]
    )
    return text, keyboard


async def _send_reminder(
    context: ContextTypes.DEFAULT_TYPE, order, threshold_count: int, percents: list[int]
) -> bool:
    """Sends the reminder and, only on success, records reminders_sent — if
    delivery fails the next pass retries at the same threshold rather than
    silently skipping a customer forever over one transient failure."""
    order_id = order["id"]
    text, keyboard = _reminder_content(order, threshold_count, percents)
    try:
        await context.bot.send_message(chat_id=order["user_id"], text=text, reply_markup=keyboard)
    except Exception:
        logger.error(
            "Sweep: failed to notify customer #%s about reminder for order #%s",
            order["user_id"],
            order_id,
            exc_info=True,
        )
        return False
    orders_repo.set_reminders_sent(order_id, threshold_count)
    return True


async def _expire_order(context: ContextTypes.DEFAULT_TYPE, order) -> bool:
    """Sets status to expired regardless of notification outcome — the
    status change must never be rolled back by a delivery failure. Does not
    restore the cart: an expired order is stale, and silently repopulating a
    cart days later would confuse more than help."""
    lang = _lang_for(order)
    order_id = order["id"]
    orders_repo.set_status(order_id, "expired", actor="system")

    order_heading = bold(t("checkout.order_heading", lang, id=order_id))
    text = t("jobs.expired", lang, order_heading=order_heading)
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
            "Sweep: failed to notify customer #%s about expiry of order #%s",
            order["user_id"],
            order_id,
            exc_info=True,
        )
        return False


async def run_sweep(context: ContextTypes.DEFAULT_TYPE) -> dict:
    """One pass over the awaiting_payment candidates. Never touches rejected
    orders or any other status — list_awaiting_payment_for_sweep() already
    filters to exactly 'awaiting_payment'. Returns a summary dict used by
    both the scheduled job's log line and the /sweep command's reply."""
    candidates = orders_repo.list_awaiting_payment_for_sweep()
    percents = _current_percents()
    large_batch = len(candidates) > RATE_LIMIT_BATCH_THRESHOLD

    examined = 0
    reminded = 0
    expired = 0
    notify_failures = 0

    for order in candidates:
        examined += 1
        notified_this_order = False
        try:
            fraction = _elapsed_fraction(order)
            if fraction >= 1.0:
                ok = await _expire_order(context, order)
                expired += 1
                notified_this_order = True
                if not ok:
                    notify_failures += 1
            else:
                passed = _thresholds_passed(fraction, percents)
                if passed > order["reminders_sent"]:
                    ok = await _send_reminder(context, order, passed, percents)
                    reminded += 1
                    notified_this_order = True
                    if not ok:
                        notify_failures += 1
        except Exception:
            logger.error("Sweep: unexpected error processing order #%s", order["id"], exc_info=True)

        if large_batch and notified_this_order:
            await asyncio.sleep(RATE_LIMIT_SLEEP_SECONDS)

    return {
        "examined": examined,
        "reminded": reminded,
        "expired": expired,
        "notify_failures": notify_failures,
    }


async def sweep_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """The recurring JobQueue callback. Wrapped in try/except so a bug in one
    pass doesn't kill the job for the lifetime of the process."""
    try:
        summary = await run_sweep(context)
        logger.info(
            "Sweep: examined=%d reminded=%d expired=%d notify_failures=%d",
            summary["examined"],
            summary["reminded"],
            summary["expired"],
            summary["notify_failures"],
        )
    except Exception:
        logger.error("Sweep: unexpected error during scheduled pass", exc_info=True)
