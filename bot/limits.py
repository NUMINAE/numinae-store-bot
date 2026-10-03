"""Shared limits. MAX_ORDER_STARS is Telegram's cap on a single Stars invoice
(telegram.constants.InvoiceLimit.MAX_STAR_COUNT; a test keeps them equal). It
bounds both the largest price an admin may set for one edition and the largest
order total checkout will accept — in mock mode too, so the demo behaves like
production."""

from __future__ import annotations

MAX_ORDER_STARS = 25000
