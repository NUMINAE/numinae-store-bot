"""Refunding an order from the admin side.

Order of operations is deliberate: the provider refund happens FIRST. If it
fails nothing in the database changes. Only after the money has really gone back
do we move the order to 'refunded' and revoke its sold keys (one transaction,
orders.mark_refunded). If that second step fails after a successful provider
refund the outcome is DB_FAILED_AFTER_REFUND, which the caller must surface to
the owner loudly so it can be reconciled by hand.

The provider is chosen by the ORDER's payment_mode, never the mode the bot is
running in: a mock order never reaches Telegram, and a Stars order always does,
even if the bot is now running in mock mode. Telling the customer is the
handler layer's job."""

from __future__ import annotations

import enum
import logging
from typing import NamedTuple

from telegram import Bot

from .payments.registry import build_provider
from .redact import redact
from .repo import orders as orders_repo

logger = logging.getLogger(__name__)

# Order ids with a refund running in this process; an overlapping call returns
# IN_FLIGHT without touching the provider (double-tapped Refund button).
_in_flight: set[int] = set()

_DETAIL_LIMIT = 120


class RefundStatus(enum.Enum):
    REFUNDED = "refunded"
    NOT_FOUND = "not_found"
    ALREADY_REFUNDED = "already_refunded"
    NOT_REFUNDABLE = "not_refundable"
    NO_CHARGE_ID = "no_charge_id"
    PROVIDER_FAILED = "provider_failed"
    DB_FAILED_AFTER_REFUND = "db_failed_after_refund"
    IN_FLIGHT = "in_flight"


class RefundResult(NamedTuple):
    status: RefundStatus
    detail: str = ""


def _short(text: str) -> str:
    """A one-line, token-free, bounded detail. Exception text from the HTTP layer
    can quote a request URL that contains the bot token; it is redacted here, at
    the source (HTML escaping still happens when the detail is rendered)."""
    text = redact(" ".join(text.split()))
    return text if len(text) <= _DETAIL_LIMIT else text[: _DETAIL_LIMIT - 1] + "…"


async def refund_order(bot: Bot, order_id: int, actor: str) -> RefundResult:
    if order_id in _in_flight:
        return RefundResult(RefundStatus.IN_FLIGHT)
    _in_flight.add(order_id)
    try:
        return await _refund_order(bot, order_id, actor)
    finally:
        _in_flight.discard(order_id)


async def _refund_order(bot: Bot, order_id: int, actor: str) -> RefundResult:
    order = orders_repo.get(order_id)
    if order is None:
        return RefundResult(RefundStatus.NOT_FOUND)
    if order["status"] == "refunded":
        return RefundResult(RefundStatus.ALREADY_REFUNDED)
    if order["status"] not in ("paid", "delivered"):
        return RefundResult(RefundStatus.NOT_REFUNDABLE, f"order is {order['status']}")

    mode = order["payment_mode"]
    charge_id = order["telegram_payment_charge_id"] or ""
    if mode == "stars" and not charge_id:
        return RefundResult(RefundStatus.NO_CHARGE_ID)

    provider = build_provider(mode)
    try:
        refunded = await provider.refund(bot, order["user_id"], charge_id)
    except Exception as exc:
        # No exc_info: the exception text may quote a URL carrying the bot token.
        detail = _short(f"{type(exc).__name__}: {exc}")
        logger.error("Refund call raised (order_id=%s): %s", order_id, detail)
        return RefundResult(RefundStatus.PROVIDER_FAILED, detail)
    if not refunded:
        logger.error("Refund declined by the provider (order_id=%s)", order_id)
        return RefundResult(RefundStatus.PROVIDER_FAILED, "the provider did not confirm the refund")

    try:
        revoked = orders_repo.mark_refunded(order_id, actor)
    except Exception as exc:
        # Money is back with the customer but our records still say paid.
        logger.error(
            "Refund succeeded but the database update failed (order_id=%s, charge_id=%s)",
            order_id,
            charge_id,
            exc_info=True,
        )
        return RefundResult(RefundStatus.DB_FAILED_AFTER_REFUND, _short(type(exc).__name__))
    return RefundResult(RefundStatus.REFUNDED, f"{revoked} key(s) revoked")
