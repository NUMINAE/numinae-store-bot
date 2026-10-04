"""Per-user update processing.

python-telegram-bot processes updates one at a time unless told otherwise, so one
slow handler (an owner's /sweep, a retried delivery, a refund) delays every other
customer's update, including a pre_checkout_query that Telegram wants answered
within 10 seconds. PerUserUpdateProcessor runs different users' updates
concurrently while keeping each user's own updates strictly in order.

How it fits PTB: BaseUpdateProcessor.process_update (final) acquires a shared
semaphore of `max_concurrent_updates` slots and THEN calls do_process_update, so a
slot is already taken when this class runs. Everything that could hold a slot for a
long time therefore has to be bounded here: each user may have at most
`max_per_user` updates queued or in flight, and an extra one is dropped at once
(releasing its slot) instead of waiting behind that user's own slow handler. With
256 slots and 8 per user, one user can hold at most 8 slots, so no single user
can starve the others.

Rules:
- A pre_checkout_query and a successful_payment update are NEVER dropped.
- A pre_checkout_query bypasses the per-user lock (see the comment at the bypass).
- A successful_payment goes through the lock, so a user's payment is recorded
  before any later update of theirs (for example a cancel) is processed.
- Updates with no user are processed without a lock.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Any

from telegram.ext import BaseUpdateProcessor

logger = logging.getLogger(__name__)

DEFAULT_MAX_CONCURRENT_UPDATES = 256
DEFAULT_MAX_PER_USER = 8


@dataclass
class _UserState:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    active: int = 0  # this user's updates that are queued or in flight


def _user_id(update: object) -> int | None:
    user = getattr(update, "effective_user", None)
    return getattr(user, "id", None)


def _is_pre_checkout(update: object) -> bool:
    return getattr(update, "pre_checkout_query", None) is not None


def _is_successful_payment(update: object) -> bool:
    message = getattr(update, "message", None)
    return message is not None and getattr(message, "successful_payment", None) is not None


class PerUserUpdateProcessor(BaseUpdateProcessor):
    def __init__(
        self,
        max_concurrent_updates: int = DEFAULT_MAX_CONCURRENT_UPDATES,
        max_per_user: int = DEFAULT_MAX_PER_USER,
    ) -> None:
        super().__init__(max_concurrent_updates)
        if max_per_user < 1:
            raise ValueError("`max_per_user` must be a positive integer!")
        self._max_per_user = max_per_user
        # user id -> state. An entry exists only while that user has an update
        # queued or in flight, so the map is empty when the bot is idle.
        self._users: dict[int, _UserState] = {}

    @property
    def tracked_users(self) -> int:
        """How many users currently have a lock entry (0 when idle)."""
        return len(self._users)

    def pending_for(self, user_id: int) -> int:
        state = self._users.get(user_id)
        return state.active if state is not None else 0

    async def do_process_update(self, update: object, coroutine: Awaitable[Any]) -> None:
        user_id = _user_id(update)
        if user_id is None:
            await coroutine
            return

        pre_checkout = _is_pre_checkout(update)
        exempt = pre_checkout or _is_successful_payment(update)

        # No await between the get/create, the cap check and the increment, so
        # these steps cannot interleave with another update's.
        state = self._users.get(user_id)
        if state is None:
            state = self._users[user_id] = _UserState()
        if state.active >= self._max_per_user and not exempt:
            logger.info("Dropped an update: user %s is over the per-user limit", user_id)
            close = getattr(coroutine, "close", None)
            if close is not None:
                close()  # never awaited: silence "coroutine was never awaited"
            return

        state.active += 1
        try:
            if pre_checkout:
                # Bypasses the per-user lock on purpose: Telegram wants the answer
                # within 10 seconds and it must never wait behind this user's other
                # (possibly slow) updates. That is safe because the pre-checkout
                # handler has no await between check_payable and extend_reservation,
                # and every database section in the bot is synchronous (no await
                # while a transaction is open), so it cannot interleave with another
                # handler's database work. Both facts are enforced by AST tests.
                await coroutine
            else:
                async with state.lock:
                    await coroutine
        finally:
            state.active -= 1
            if state.active == 0 and self._users.get(user_id) is state:
                del self._users[user_id]

    async def initialize(self) -> None:
        """Nothing to allocate."""

    async def shutdown(self) -> None:
        """Nothing to release."""
