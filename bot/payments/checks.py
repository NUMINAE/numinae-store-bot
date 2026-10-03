"""The one payability check. Both the pre-checkout handler (real Stars
payments) and the demo "Pay" button run it, so the mock path obeys exactly the
same rules as the real one: the order must exist, belong to the payer, still be
pending_payment and unexpired, use the running payment mode, match the amount
(and, for Stars, the XTR currency), and still hold all its key reservations."""

from __future__ import annotations

import enum
import sqlite3

from ..repo import license_keys as keys_repo
from ..repo import orders as orders_repo

STARS_MODE = "stars"


class PayRejection(enum.Enum):
    NOT_FOUND = "not_found"
    NOT_OWNER = "not_owner"
    NOT_PENDING = "not_pending"
    EXPIRED = "expired"
    WRONG_MODE = "wrong_mode"
    WRONG_CURRENCY = "wrong_currency"
    WRONG_AMOUNT = "wrong_amount"
    RESERVATION_LOST = "reservation_lost"


# The customer-facing text for each rejection. A missing order and someone
# else's order deliberately share one message, so the response cannot be used
# to probe which order ids exist.
REJECTION_MESSAGE_KEY: dict[PayRejection, str] = {
    PayRejection.NOT_FOUND: "pay.err_unavailable",
    PayRejection.NOT_OWNER: "pay.err_unavailable",
    PayRejection.NOT_PENDING: "pay.err_not_pending",
    PayRejection.EXPIRED: "pay.err_expired",
    PayRejection.WRONG_MODE: "pay.err_mode",
    PayRejection.WRONG_CURRENCY: "pay.err_mode",
    PayRejection.WRONG_AMOUNT: "pay.err_amount",
    PayRejection.RESERVATION_LOST: "pay.err_reservation",
}


def check_payable(
    order: sqlite3.Row | None,
    payer_id: int,
    amount: int,
    currency: str,
    mode: str,
) -> PayRejection | None:
    """Why this payment must be refused, or None if it may proceed. `mode` is
    the payment mode the bot is running in. No network calls."""
    if order is None:
        return PayRejection.NOT_FOUND
    if order["user_id"] != payer_id:
        return PayRejection.NOT_OWNER
    if order["status"] != "pending_payment":
        return PayRejection.NOT_PENDING
    if orders_repo.is_expired(order):
        return PayRejection.EXPIRED
    if order["payment_mode"] != mode:
        return PayRejection.WRONG_MODE
    if mode == STARS_MODE and currency != orders_repo.STARS_CURRENCY:
        return PayRejection.WRONG_CURRENCY
    if amount != order["total_stars"]:
        return PayRejection.WRONG_AMOUNT
    needed = orders_repo.reserved_key_quantity_needed(order["id"])
    if keys_repo.count_reserved_for_order(order["id"]) != needed:
        return PayRejection.RESERVATION_LOST
    return None
