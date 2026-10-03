"""Query functions for orders, order_items, and order_events.

set_status() is the only function anywhere in this codebase that writes
orders.status; it records the order_events row in the same transaction, so the
allowed-transitions map lives here as a shared module-level constant. Order
creation (with licence-key reservation) arrives with checkout.
"""

from __future__ import annotations

import sqlite3

from ..db import get_conn, transaction

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


class OrderError(Exception):
    """Base class for order domain errors."""


class IllegalTransitionError(OrderError):
    def __init__(self, from_status: str, to_status: str) -> None:
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(f"Cannot transition order from {from_status!r} to {to_status!r}.")


def _assert_transition_allowed(from_status: str, to_status: str) -> None:
    if to_status not in ALLOWED_TRANSITIONS.get(from_status, frozenset()):
        raise IllegalTransitionError(from_status, to_status)


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


def set_status(order_id: int, new_status: str, actor: str, note: str | None = None) -> None:
    with transaction() as conn:
        order = conn.execute("SELECT status FROM orders WHERE id = ?", (order_id,)).fetchone()
        if order is None:
            raise OrderError(f"Order {order_id} not found.")
        from_status = order["status"]
        _assert_transition_allowed(from_status, new_status)

        stamp_column = _STATUS_TIMESTAMP_COLUMN.get(new_status)
        stamp = f", {stamp_column} = datetime('now')" if stamp_column else ""
        conn.execute(
            f"UPDATE orders SET status = ?, updated_at = datetime('now'){stamp} WHERE id = ?",  # noqa: S608 - column from a fixed map
            (new_status, order_id),
        )
        conn.execute(
            """
            INSERT INTO order_events (order_id, from_status, to_status, actor, note)
            VALUES (?, ?, ?, ?, ?)
            """,
            (order_id, from_status, new_status, actor, note),
        )
