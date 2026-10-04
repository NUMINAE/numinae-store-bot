"""The payment provider interface. A provider presents the checkout for a
freshly created order and can refund a recorded payment. Providers never touch
order state: completing a payment goes through repo.orders.complete_payment()
from the handlers, whichever provider took the money."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from typing import Protocol

from telegram import Bot


class PaymentProvider(Protocol):
    mode: str

    async def present_checkout(
        self,
        bot: Bot,
        chat_id: int,
        order: sqlite3.Row,
        items: Sequence[sqlite3.Row],
    ) -> None:
        """Show the customer how to pay for this pending order."""
        ...

    async def refund(self, bot: Bot, user_id: int, charge_id: str) -> bool:
        """Refund the payment; True on success, False on failure (never raises)."""
        ...
