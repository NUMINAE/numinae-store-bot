"""Rate-limited owner notifications for new customers.

Kept separate from bot/ratelimit.py by design: that module protects the bot
FROM users (per-user throttling); this protects the owner FROM the bot
during a signup burst.

In-memory only, no persistence. A discrete one-hour window opens on the
first new customer, allows up to LIMIT_PER_WINDOW individual notifications,
suppresses (and counts) the rest, and — via a one-shot JobQueue job
scheduled the moment the window opens — sends a single rollup message for
anything suppressed once the hour is up, then resets so the next new
customer opens a fresh window. Losing this state on a restart is
acceptable (see the feature request): worst case is a few extra individual
notifications after a restart mid-burst.
"""

from __future__ import annotations

import logging

from telegram import User
from telegram.ext import ContextTypes

from .config import get_settings
from .i18n import tn
from .render import code, esc
from .repo import customers as customers_repo
from .repo import settings as settings_repo
from .ui_kit import ICON, screen, trim

logger = logging.getLogger(__name__)

LIMIT_PER_WINDOW = 5
NAME_BUDGET = 64
WINDOW_SECONDS = 3600
_ROLLUP_JOB_NAME = "new_customer_rollup"

_window_open = False
_sent_count = 0
_suppressed_count = 0


def _notifications_enabled() -> bool:
    return settings_repo.get("notify_new_customers", "1") == "1"


async def notify_new_customer(context: ContextTypes.DEFAULT_TYPE, user: User) -> None:
    """Call only when repo.customers.upsert() reported a newly created row,
    and only after the customer's own /start reply has already been sent."""
    if not _notifications_enabled():
        return

    global _window_open, _sent_count, _suppressed_count

    if not _window_open:
        _window_open = True
        _sent_count = 0
        _suppressed_count = 0
        context.job_queue.run_once(_rollup_job, WINDOW_SECONDS, name=_ROLLUP_JOB_NAME)  # type: ignore[union-attr]  # job_queue is configured in main.py

    if _sent_count < LIMIT_PER_WINDOW:
        _sent_count += 1
        await _send_individual(context, user)
    else:
        _suppressed_count += 1


async def _send_individual(context: ContextTypes.DEFAULT_TYPE, user: User) -> None:
    lines = [esc(trim(user.first_name, NAME_BUDGET)) if user.first_name else "(no name)"]
    if user.username:
        lines.append(f"@{esc(trim(user.username, NAME_BUDGET))}")
    lines.append(code(user.id))
    lines.append(f"customer #{customers_repo.count_all()}")
    text = screen(ICON.info, "New customer", "\n".join(lines))
    try:
        await context.bot.send_message(get_settings().owner_id, text)
    except Exception:
        logger.error(
            "Failed to send new-customer notification for user #%s", user.id, exc_info=True
        )


async def _rollup_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    global _window_open, _sent_count, _suppressed_count
    suppressed = _suppressed_count
    _window_open = False
    _sent_count = 0
    _suppressed_count = 0

    if suppressed <= 0 or not _notifications_enabled():
        return

    text = tn("owner.more_customers", suppressed)
    try:
        await context.bot.send_message(get_settings().owner_id, text)
    except Exception:
        logger.error("Failed to send new-customer rollup notification", exc_info=True)
