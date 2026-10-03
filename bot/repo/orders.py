"""Query functions for orders, order_items, and order_events.

set_status() is the only function anywhere in this codebase that writes
orders.status; it records the order_events row in the same transaction, so the
allowed-transitions map lives here as a shared module-level constant. Compound
operations (creating an order with key reservations, completing a payment,
cancelling, expiring) combine a status change with key and cart writes in ONE
transaction by calling set_status_in_txn() on their own connection.

Time is SQLite's UTC datetime('now') throughout; reservations and expiry
compare those text timestamps, which sort chronologically.
"""

from __future__ import annotations

import enum
import logging
import sqlite3
from collections.abc import Mapping
from typing import NamedTuple

from ..db import get_conn, immediate_transaction, transaction
from ..limits import MAX_ORDER_STARS
from . import cart as cart_repo
from . import license_keys as keys_repo
from . import products as products_repo
from . import settings as settings_repo

logger = logging.getLogger(__name__)

ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending_payment": frozenset({"paid", "cancelled", "expired"}),
    "paid": frozenset({"delivered", "refunded"}),
    "delivered": frozenset({"refunded"}),
    "cancelled": frozenset(),
    "expired": frozenset(),
    "refunded": frozenset(),
}

# Stamped alongside the status when an order enters one of these states.
_STATUS_TIMESTAMP_COLUMN = {
    "paid": "paid_at",
    "delivered": "delivered_at",
    "refunded": "refunded_at",
}

# Columns set_status() may write besides the status itself.
EXTRA_COLUMN_WHITELIST = frozenset({"telegram_payment_charge_id"})

STARS_CURRENCY = "XTR"
DEFAULT_RESERVATION_MINUTES = 15
LIBRARY_STATUSES = ("paid", "delivered", "refunded")


class OrderError(Exception):
    """Base class for order domain errors."""


class IllegalTransitionError(OrderError):
    def __init__(self, from_status: str, to_status: str) -> None:
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(f"Cannot transition order from {from_status!r} to {to_status!r}.")


class OpenOrderExists(OrderError):
    def __init__(self, order_id: int) -> None:
        self.order_id = order_id
        super().__init__(f"Customer already has an open order #{order_id}.")


class EmptyCartError(OrderError):
    def __init__(self) -> None:
        super().__init__("Cart has no purchasable items.")


class OrderTooLarge(OrderError):
    def __init__(self, total: int) -> None:
        self.total = total
        super().__init__(f"Order total {total} exceeds the {MAX_ORDER_STARS} Star limit.")


class InsufficientStock(OrderError):
    def __init__(self, variant_id: int, available: int) -> None:
        self.variant_id = variant_id
        self.available = available
        super().__init__(f"Edition {variant_id} has only {available} key(s) available.")


class CreatedOrder(NamedTuple):
    """Result of create_order()."""

    order_id: int
    dropped_count: int


class PaymentOutcome(enum.Enum):
    PAID = "paid"
    DUPLICATE = "duplicate"
    ORPHAN = "orphan"
    MISMATCH = "mismatch"


class OrphanReason(enum.Enum):
    """Why a payment could not be applied (outcome ORPHAN)."""

    EXPIRED_OR_CANCELLED = "expired_or_cancelled"
    ALREADY_PAID = "already_paid"  # a second charge for an order already settled


class PaymentResult(NamedTuple):
    outcome: PaymentOutcome
    reason: OrphanReason | None = None  # set when outcome is ORPHAN
    short_keys: tuple[int, int] | None = None  # (sold, needed) if the reservation fell short


def _assert_transition_allowed(from_status: str, to_status: str) -> None:
    if to_status not in ALLOWED_TRANSITIONS.get(from_status, frozenset()):
        raise IllegalTransitionError(from_status, to_status)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def get(order_id: int) -> sqlite3.Row | None:
    conn = get_conn()
    return conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()


def get_with_items(order_id: int) -> tuple[sqlite3.Row | None, list[sqlite3.Row]]:
    conn = get_conn()
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if order is None:
        return None, []
    items = conn.execute(
        "SELECT * FROM order_items WHERE order_id = ? ORDER BY id", (order_id,)
    ).fetchall()
    return order, items


def list_for_user(user_id: int, limit: int, offset: int) -> list[sqlite3.Row]:
    conn = get_conn()
    return conn.execute(
        """
        SELECT * FROM orders
        WHERE user_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT ? OFFSET ?
        """,
        (user_id, limit, offset),
    ).fetchall()


def get_open_for_user(user_id: int) -> sqlite3.Row | None:
    """The customer's pending_payment order, if any (there is at most one)."""
    return (
        get_conn()
        .execute(
            "SELECT * FROM orders WHERE user_id = ? AND status = 'pending_payment'", (user_id,)
        )
        .fetchone()
    )


def list_events(order_id: int) -> list[sqlite3.Row]:
    return (
        get_conn()
        .execute("SELECT * FROM order_events WHERE order_id = ? ORDER BY id", (order_id,))
        .fetchall()
    )


# ---------------------------------------------------------------------------
# Status changes
# ---------------------------------------------------------------------------


def set_status_in_txn(
    conn: sqlite3.Connection,
    order_id: int,
    new_status: str,
    actor: str,
    note: str | None = None,
    extra: Mapping[str, object] | None = None,
) -> None:
    """The one place orders.status is written. Runs on the caller's connection
    and transaction (it never commits): the status change, any whitelisted
    extra columns and the order_events row land or roll back together."""
    extra = extra or {}
    forbidden = set(extra) - EXTRA_COLUMN_WHITELIST
    if forbidden:
        raise ValueError(f"Columns not allowed in set_status extra: {sorted(forbidden)}")

    order = conn.execute("SELECT status FROM orders WHERE id = ?", (order_id,)).fetchone()
    if order is None:
        raise OrderError(f"Order {order_id} not found.")
    from_status = order["status"]
    _assert_transition_allowed(from_status, new_status)

    assignments = ["status = ?", "updated_at = datetime('now')"]
    values: list[object] = [new_status]
    stamp_column = _STATUS_TIMESTAMP_COLUMN.get(new_status)
    if stamp_column:
        assignments.append(f"{stamp_column} = datetime('now')")
    for column, value in extra.items():  # column names are whitelisted above
        assignments.append(f"{column} = ?")
        values.append(value)
    conn.execute(
        f"UPDATE orders SET {', '.join(assignments)} WHERE id = ?",  # noqa: S608 - whitelisted columns
        (*values, order_id),
    )
    conn.execute(
        """
        INSERT INTO order_events (order_id, from_status, to_status, actor, note)
        VALUES (?, ?, ?, ?, ?)
        """,
        (order_id, from_status, new_status, actor, note),
    )


def set_status(
    order_id: int,
    new_status: str,
    actor: str,
    note: str | None = None,
    extra: Mapping[str, object] | None = None,
) -> None:
    with transaction() as conn:
        set_status_in_txn(conn, order_id, new_status, actor, note, extra)


def add_note_in_txn(conn: sqlite3.Connection, order_id: int, actor: str, note: str) -> None:
    order = conn.execute("SELECT status FROM orders WHERE id = ?", (order_id,)).fetchone()
    if order is None:
        raise OrderError(f"Order {order_id} not found.")
    conn.execute(
        """
        INSERT INTO order_events (order_id, from_status, to_status, actor, note)
        VALUES (?, ?, ?, ?, ?)
        """,
        (order_id, order["status"], order["status"], actor, note),
    )


def add_note(order_id: int, actor: str, note: str) -> None:
    """Audit note: an order_events row with from_status == to_status ==
    current status. Never changes the status."""
    with transaction() as conn:
        add_note_in_txn(conn, order_id, actor, note)


# ---------------------------------------------------------------------------
# Creating an order
# ---------------------------------------------------------------------------


def _reservation_minutes() -> int:
    raw = settings_repo.get("reservation_minutes", str(DEFAULT_RESERVATION_MINUTES))
    try:
        return max(1, int(raw or DEFAULT_RESERVATION_MINUTES))
    except ValueError:
        return DEFAULT_RESERVATION_MINUTES


def _claim_keys(conn: sqlite3.Connection, variant_id: int, quantity: int) -> list[int]:
    """Ids of `quantity` claimable keys: available, or reserved with a lapsed
    reservation. Raises InsufficientStock if there aren't enough."""
    rows = conn.execute(
        """
        SELECT id FROM license_keys
        WHERE variant_id = ?
          AND (status = 'available'
               OR (status = 'reserved' AND reserved_until IS NOT NULL
                   AND reserved_until <= datetime('now')))
        ORDER BY id
        LIMIT ?
        """,
        (variant_id, quantity),
    ).fetchall()
    if len(rows) < quantity:
        raise InsufficientStock(variant_id, len(rows))
    return [row["id"] for row in rows]


def create_order(user_id: int, payment_mode: str) -> CreatedOrder:
    """Turn the customer's cart into a pending_payment order, reserving licence
    keys, in ONE BEGIN IMMEDIATE transaction. Lines that are no longer
    purchasable are dropped (and counted). On any failure nothing is written."""
    minutes = _reservation_minutes()
    try:
        with immediate_transaction() as conn:
            existing = get_open_for_user(user_id)
            if existing is not None:
                raise OpenOrderExists(existing["id"])

            lines = cart_repo.list_lines(user_id)
            purchasable = [ln for ln in lines if products_repo.is_variant_available(ln)]
            dropped = len(lines) - len(purchasable)
            if not purchasable:
                raise EmptyCartError

            total = sum(ln["line_total"] for ln in purchasable)
            if total > MAX_ORDER_STARS:
                raise OrderTooLarge(total)

            claims = {
                ln["variant_id"]: _claim_keys(conn, ln["variant_id"], ln["quantity"])
                for ln in purchasable
                if ln["delivery_kind"] == "key"
            }

            item_count = sum(ln["quantity"] for ln in purchasable)
            expires_at = conn.execute(
                "SELECT datetime('now', ?) AS t", (f"+{minutes} minutes",)
            ).fetchone()["t"]
            order_id = conn.execute(
                """
                INSERT INTO orders
                    (user_id, total_stars, item_count, payment_mode, status, expires_at)
                VALUES (?, ?, ?, ?, 'pending_payment', ?)
                """,
                (user_id, total, item_count, payment_mode, expires_at),
            ).lastrowid
            if order_id is None:
                raise OrderError("Order insert returned no row id.")

            for ln in purchasable:
                item_id = conn.execute(
                    """
                    INSERT INTO order_items
                        (order_id, variant_id, product_name, variant_label, delivery_kind,
                         unit_price_stars, quantity, line_total_stars)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        order_id,
                        ln["variant_id"],
                        ln["product_name"],
                        ln["variant_label"],
                        ln["delivery_kind"],
                        ln["price_stars"],
                        ln["quantity"],
                        ln["line_total"],
                    ),
                ).lastrowid
                for key_id in claims.get(ln["variant_id"], []):
                    conn.execute(
                        """
                        UPDATE license_keys
                        SET status = 'reserved', order_item_id = ?, reserved_until = ?
                        WHERE id = ?
                        """,
                        (item_id, expires_at, key_id),
                    )

            conn.execute(
                """
                INSERT INTO order_events (order_id, from_status, to_status, actor, note)
                VALUES (?, NULL, 'pending_payment', 'customer', 'Order created')
                """,
                (order_id,),
            )
            conn.execute("DELETE FROM cart_items WHERE user_id = ?", (user_id,))
    except sqlite3.IntegrityError:
        existing = get_open_for_user(user_id)
        if existing is not None:
            raise OpenOrderExists(existing["id"]) from None
        raise
    return CreatedOrder(order_id, dropped)


def extend_reservation(order_id: int, minutes: int) -> bool:
    """Push the order's expiry and its reserved keys' reserved_until forward to
    at least now + minutes. Only pending_payment orders are touched; returns
    whether one was."""
    horizon = f"+{minutes} minutes"
    with transaction() as conn:
        cur = conn.execute(
            """
            UPDATE orders
            SET expires_at = MAX(COALESCE(expires_at, ''), datetime('now', ?)),
                updated_at = datetime('now')
            WHERE id = ? AND status = 'pending_payment'
            """,
            (horizon, order_id),
        )
        if cur.rowcount == 0:
            return False
        conn.execute(
            """
            UPDATE license_keys
            SET reserved_until = MAX(COALESCE(reserved_until, ''), datetime('now', ?))
            WHERE status = 'reserved'
              AND order_item_id IN (SELECT id FROM order_items WHERE order_id = ?)
            """,
            (horizon, order_id),
        )
        return True


def reserved_key_quantity_needed(order_id: int) -> int:
    """Keys this order must hold reserved: the summed quantity of its key lines."""
    row = (
        get_conn()
        .execute(
            "SELECT COALESCE(SUM(quantity), 0) AS n FROM order_items"
            " WHERE order_id = ? AND delivery_kind = 'key'",
            (order_id,),
        )
        .fetchone()
    )
    return int(row["n"])


def is_expired(order: sqlite3.Row) -> bool:
    expires_at = order["expires_at"]
    if expires_at is None:
        return False
    now = get_conn().execute("SELECT datetime('now') AS t").fetchone()["t"]
    return bool(expires_at <= now)


def minutes_left(order: sqlite3.Row) -> int:
    """Whole minutes until the order expires, rounded up; 0 once it has."""
    if order["expires_at"] is None:
        return 0
    row = (
        get_conn()
        .execute("SELECT (julianday(?) - julianday('now')) * 1440 AS m", (order["expires_at"],))
        .fetchone()
    )
    remaining = row["m"]
    return max(0, int(-(-remaining // 1))) if remaining > 0 else 0


def paid_longer_than(order: sqlite3.Row, minutes: int) -> bool:
    """Whether the order was paid more than `minutes` ago."""
    if order["paid_at"] is None:
        return False
    row = (
        get_conn()
        .execute("SELECT ? <= datetime('now', ?) AS old", (order["paid_at"], f"-{minutes} minutes"))
        .fetchone()
    )
    return bool(row["old"])


# ---------------------------------------------------------------------------
# Payment, cancellation, expiry
# ---------------------------------------------------------------------------


def complete_payment(
    order_id: int, payer_id: int, charge_id: str, amount: int, currency: str
) -> PaymentResult:
    """Record a successful payment. The outcome is one of:

    MISMATCH (no state change): the order is missing, belongs to someone else,
    or the amount / currency don't match.
    DUPLICATE: this charge was already recorded (no state change).
    ORPHAN: the order can no longer be paid. An audit note carrying the charge
    id is written and the caller must refund the INCOMING charge. The reason
    says whether the order expired or was cancelled (EXPIRED_OR_CANCELLED) or
    was already settled by an earlier charge (ALREADY_PAID).
    PAID: the order moves to 'paid' and its reserved keys to 'sold' in one
    transaction. If fewer keys were sold than the order needs, short_keys
    carries (sold, needed) so the caller can alert the owner at once.
    """
    try:
        with immediate_transaction() as conn:
            order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
            if order is None or order["user_id"] != payer_id:
                return PaymentResult(PaymentOutcome.MISMATCH)
            if amount != order["total_stars"]:
                return PaymentResult(PaymentOutcome.MISMATCH)
            if order["payment_mode"] == "stars" and currency != STARS_CURRENCY:
                return PaymentResult(PaymentOutcome.MISMATCH)
            if order["telegram_payment_charge_id"] == charge_id:
                return PaymentResult(PaymentOutcome.DUPLICATE)
            if order["status"] != "pending_payment":
                add_note_in_txn(
                    conn,
                    order_id,
                    "system",
                    f"Payment received while order was {order['status']}; charge {charge_id}",
                )
                reason = (
                    OrphanReason.EXPIRED_OR_CANCELLED
                    if order["status"] in ("expired", "cancelled")
                    else OrphanReason.ALREADY_PAID
                )
                return PaymentResult(PaymentOutcome.ORPHAN, reason)

            set_status_in_txn(
                conn,
                order_id,
                "paid",
                "system",
                note="Payment received",
                extra={"telegram_payment_charge_id": charge_id},
            )
            sold = keys_repo.sell_reserved_for_order_in(conn, order_id)
            needed = conn.execute(
                "SELECT COALESCE(SUM(quantity), 0) AS n FROM order_items"
                " WHERE order_id = ? AND delivery_kind = 'key'",
                (order_id,),
            ).fetchone()["n"]
            if sold < needed:
                add_note_in_txn(
                    conn,
                    order_id,
                    "system",
                    f"Key reservation incomplete at payment: {sold} of {needed} keys sold",
                )
                return PaymentResult(PaymentOutcome.PAID, short_keys=(sold, needed))
            return PaymentResult(PaymentOutcome.PAID)
    except sqlite3.IntegrityError as exc:
        if "telegram_payment_charge_id" not in str(exc):
            raise
        # The charge id is already attached to another order: a replayed payment.
        return PaymentResult(PaymentOutcome.DUPLICATE)


def _restore_cart_lines(conn: sqlite3.Connection, order_id: int, user_id: int) -> int:
    restored = 0
    items = conn.execute("SELECT * FROM order_items WHERE order_id = ?", (order_id,)).fetchall()
    for item in items:
        if item["variant_id"] is None:
            continue
        variant = products_repo.get_variant(item["variant_id"])
        if variant is None or not products_repo.is_variant_available(variant):
            continue
        limit = cart_repo.max_quantity_for(variant["delivery_kind"], variant["key_stock"])
        existing = conn.execute(
            "SELECT quantity FROM cart_items WHERE user_id = ? AND variant_id = ?",
            (user_id, item["variant_id"]),
        ).fetchone()
        current = existing["quantity"] if existing else 0
        target = min(current + item["quantity"], limit)
        if target <= current:
            continue
        conn.execute(
            """
            INSERT INTO cart_items (user_id, variant_id, quantity) VALUES (?, ?, ?)
            ON CONFLICT(user_id, variant_id) DO UPDATE SET quantity = excluded.quantity
            """,
            (user_id, item["variant_id"], target),
        )
        restored += 1
    return restored


def cancel_order(order_id: int, actor: str) -> int:
    """pending_payment -> cancelled, releasing the order's reserved keys. For
    actor 'customer' the purchasable lines go back into the cart (clamped by the
    cart limits). One transaction; returns the number of lines restored."""
    with immediate_transaction() as conn:
        order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if order is None:
            raise OrderError(f"Order {order_id} not found.")
        set_status_in_txn(conn, order_id, "cancelled", actor, note=f"Cancelled by {actor}")
        keys_repo.release_for_order_in(conn, order_id)
        if actor == "customer":
            return _restore_cart_lines(conn, order_id, order["user_id"])
        return 0


def expire_due_orders() -> list[tuple[int, int]]:
    """Expire every pending_payment order past its expires_at: status 'expired'
    (actor 'system') and its keys released, one transaction per order. Never
    restores the cart. One order failing is logged and does not stop the rest.
    Returns (order_id, user_id) for each order expired."""
    due = (
        get_conn()
        .execute(
            """
            SELECT id, user_id FROM orders
            WHERE status = 'pending_payment' AND expires_at IS NOT NULL
              AND expires_at <= datetime('now')
            ORDER BY id
            """
        )
        .fetchall()
    )
    expired: list[tuple[int, int]] = []
    for row in due:
        try:
            with immediate_transaction() as conn:
                set_status_in_txn(conn, row["id"], "expired", "system", note="Reservation expired")
                keys_repo.release_for_order_in(conn, row["id"])
        except IllegalTransitionError:
            continue  # settled by someone else between the scan and this transaction
        except Exception:
            logger.error("Failed to expire order %s", row["id"], exc_info=True)
            continue
        expired.append((row["id"], row["user_id"]))
    return expired


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------


def list_paid_undelivered_orders() -> list[sqlite3.Row]:
    return (
        get_conn()
        .execute("SELECT * FROM orders WHERE status = 'paid' ORDER BY paid_at, id")
        .fetchall()
    )


def list_undelivered_items(order_id: int) -> list[sqlite3.Row]:
    return (
        get_conn()
        .execute(
            "SELECT * FROM order_items WHERE order_id = ? AND delivered_at IS NULL ORDER BY id",
            (order_id,),
        )
        .fetchall()
    )


def mark_item_delivered(order_item_id: int) -> None:
    with transaction() as conn:
        conn.execute(
            "UPDATE order_items SET delivered_at = datetime('now')"
            " WHERE id = ? AND delivered_at IS NULL",
            (order_item_id,),
        )


def finish_order_if_delivered(order_id: int) -> bool:
    """Move a paid order to 'delivered' (via set_status) once every item has
    its delivered_at. Returns whether the order was moved."""
    with immediate_transaction() as conn:
        order = conn.execute("SELECT status FROM orders WHERE id = ?", (order_id,)).fetchone()
        if order is None or order["status"] != "paid":
            return False
        pending = conn.execute(
            "SELECT COUNT(*) AS n FROM order_items WHERE order_id = ? AND delivered_at IS NULL",
            (order_id,),
        ).fetchone()["n"]
        if pending:
            return False
        set_status_in_txn(conn, order_id, "delivered", "system", note="All items delivered")
        return True


# ---------------------------------------------------------------------------
# Library
# ---------------------------------------------------------------------------

_LIBRARY_FROM = """
    FROM order_items oi
    JOIN orders o ON o.id = oi.order_id
    WHERE o.user_id = ? AND o.status IN ('paid', 'delivered', 'refunded')
"""


def list_library_items(user_id: int, limit: int, offset: int) -> list[sqlite3.Row]:
    """Items of the customer's paid, delivered or refunded orders, newest first."""
    return (
        get_conn()
        .execute(
            f"""
            SELECT oi.*, o.status AS order_status, o.paid_at AS paid_at
            {_LIBRARY_FROM}
            ORDER BY o.created_at DESC, o.id DESC, oi.id
            LIMIT ? OFFSET ?
            """,  # noqa: S608 - _LIBRARY_FROM is a fixed constant fragment
            (user_id, limit, offset),
        )
        .fetchall()
    )


def count_library_items(user_id: int) -> int:
    row = (
        get_conn()
        .execute(f"SELECT COUNT(*) AS n {_LIBRARY_FROM}", (user_id,))  # noqa: S608 - fixed constant
        .fetchone()
    )
    return int(row["n"])


def get_item_with_order(order_item_id: int) -> sqlite3.Row | None:
    """One order item joined with its order's user and status; any status."""
    return (
        get_conn()
        .execute(
            """
            SELECT oi.*, o.user_id AS user_id, o.status AS order_status,
                   o.paid_at AS paid_at, o.total_stars AS order_total_stars
            FROM order_items oi
            JOIN orders o ON o.id = oi.order_id
            WHERE oi.id = ?
            """,
            (order_item_id,),
        )
        .fetchone()
    )


# ---------------------------------------------------------------------------
# Admin: listing, the order card, refunds
# ---------------------------------------------------------------------------

ADMIN_FILTERS = ("all", "pending_payment", "paid", "delivered", "refunded", "closed", "attention")

# Fixed SQL fragments, chosen by a whitelisted key; user input never reaches them.
_ADMIN_WHERE = {
    "all": "1 = 1",
    "pending_payment": "o.status = 'pending_payment'",
    "paid": "o.status = 'paid'",
    "delivered": "o.status = 'delivered'",
    "refunded": "o.status = 'refunded'",
    "closed": "o.status IN ('cancelled', 'expired')",
    "attention": (
        "o.status = 'paid' AND EXISTS ("
        "SELECT 1 FROM order_items oi WHERE oi.order_id = o.id AND oi.delivered_at IS NULL)"
    ),
}


def _admin_where(filter_key: str) -> str:
    try:
        return _ADMIN_WHERE[filter_key]
    except KeyError:
        raise ValueError(f"Unknown order filter {filter_key!r}.") from None


def list_for_admin(filter_key: str, limit: int, offset: int) -> list[sqlite3.Row]:
    """Orders matching the filter, newest first."""
    where = _admin_where(filter_key)
    return (
        get_conn()
        .execute(
            f"SELECT o.* FROM orders o WHERE {where} ORDER BY o.created_at DESC, o.id DESC"  # noqa: S608 - fixed whitelisted fragment
            " LIMIT ? OFFSET ?",
            (limit, offset),
        )
        .fetchall()
    )


def count_for_admin(filter_key: str) -> int:
    where = _admin_where(filter_key)
    row = (
        get_conn()
        .execute(f"SELECT COUNT(*) AS n FROM orders o WHERE {where}")  # noqa: S608 - fixed whitelisted fragment
        .fetchone()
    )
    return int(row["n"])


def counts_by_filter() -> dict[str, int]:
    return {key: count_for_admin(key) for key in ADMIN_FILTERS}


def attention_count() -> int:
    """Paid orders with at least one undelivered item."""
    return count_for_admin("attention")


class AdminCard(NamedTuple):
    order: sqlite3.Row
    buyer: sqlite3.Row | None  # first_name, username, user_id
    items: list[sqlite3.Row]  # incl. delivery_kind and delivered_at
    events: list[sqlite3.Row]  # the last 5, oldest first


def get_admin_card(order_id: int) -> AdminCard | None:
    conn = get_conn()
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if order is None:
        return None
    buyer = conn.execute(
        "SELECT first_name, username, user_id FROM customers WHERE user_id = ?",
        (order["user_id"],),
    ).fetchone()
    items = conn.execute(
        "SELECT * FROM order_items WHERE order_id = ? ORDER BY id", (order_id,)
    ).fetchall()
    events = conn.execute(
        "SELECT * FROM order_events WHERE order_id = ? ORDER BY id DESC LIMIT 5", (order_id,)
    ).fetchall()
    return AdminCard(order, buyer, items, events[::-1])


def mark_refunded(order_id: int, actor: str, note: str | None = None) -> int:
    """paid or delivered -> refunded AND the order's sold keys -> revoked, in ONE
    transaction: either both happen or neither. Raises IllegalTransitionError
    from any other status. Returns the number of keys revoked."""
    with immediate_transaction() as conn:
        set_status_in_txn(conn, order_id, "refunded", actor, note=note or f"Refunded by {actor}")
        return keys_repo.revoke_for_order_in(conn, order_id)
