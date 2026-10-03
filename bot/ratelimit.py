"""Simple in-memory per-user rate limiting. Registered in main.py as a
TypeHandler in group -1, so it runs before every other handler and applies
uniformly to both message and callback updates without touching individual
handlers.

The realistic risk here isn't a classic flood attack — Telegram's own rate
limits bound that — it's that every DB call in this bot runs synchronously
on the single asyncio event loop (see bot/db.py), so a burst of rapid taps
from one user can create backpressure that stalls the bot for everyone else
while those queries run. This throttle caps that burst per user.
"""

from __future__ import annotations

import logging
import time
from collections import deque

from telegram import Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

from .config import CONFIG

logger = logging.getLogger(__name__)

LIMIT = 20
WINDOW_SECONDS = 10.0
_GC_INTERVAL = 500

_THROTTLE_MESSAGE = "You're going a bit fast — try again in a moment."

_hits: dict[int, deque[float]] = {}
_warned_until: dict[int, float] = {}
_calls_since_gc = 0


def _gc(now: float) -> None:
    """Drop any per-user entry whose timestamps have all aged out of the
    window, run periodically rather than on every call, so the dict doesn't
    grow without bound over the bot's lifetime as distinct users come and go."""
    cutoff = now - WINDOW_SECONDS
    stale = []
    for user_id, hits in _hits.items():
        while hits and hits[0] < cutoff:
            hits.popleft()
        if not hits:
            stale.append(user_id)
    for user_id in stale:
        del _hits[user_id]
        _warned_until.pop(user_id, None)


async def throttle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global _calls_since_gc

    user = update.effective_user
    if user is None or user.id == CONFIG.owner_id:
        return

    user_id = user.id
    now = time.monotonic()
    cutoff = now - WINDOW_SECONDS

    _calls_since_gc += 1
    if _calls_since_gc >= _GC_INTERVAL:
        _calls_since_gc = 0
        _gc(now)

    hits = _hits.setdefault(user_id, deque())
    while hits and hits[0] < cutoff:
        hits.popleft()

    exceeded = len(hits) >= LIMIT
    hits.append(now)

    if not exceeded:
        return

    if now >= _warned_until.get(user_id, 0.0):
        logger.warning("Rate limit tripped by user_id=%s", user_id)
        _warned_until[user_id] = now + WINDOW_SECONDS

    if update.callback_query:
        await update.callback_query.answer(_THROTTLE_MESSAGE, show_alert=True)
    elif update.message:
        await update.message.reply_text(_THROTTLE_MESSAGE)

    raise ApplicationHandlerStop
