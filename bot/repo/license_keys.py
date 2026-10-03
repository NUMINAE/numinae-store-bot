"""Query functions for license_keys: stock entry and counting, plus the
order-scoped reserve/sell/release operations used by repo.orders."""

from __future__ import annotations

import sqlite3

from ..db import get_conn, transaction


def add_many(variant_id: int, keys: list[str]) -> int:
    """Insert the keys as available stock. Surrounding whitespace is stripped,
    blanks are skipped, and duplicates — within the input or already stored for
    this edition — are ignored. Returns how many were actually added."""
    unique = list(dict.fromkeys(key.strip() for key in keys if key.strip()))
    added = 0
    with transaction() as conn:
        for key in unique:
            cur = conn.execute(
                "INSERT OR IGNORE INTO license_keys (variant_id, key_value) VALUES (?, ?)",
                (variant_id, key),
            )
            added += cur.rowcount
    return added


def count_available(variant_id: int) -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM license_keys WHERE variant_id = ? AND status = 'available'",
        (variant_id,),
    ).fetchone()
    return int(row["n"])


# ---------------------------------------------------------------------------
# Order-scoped key operations. The *_in functions take the caller's
# connection and never commit, so they can join a larger transaction (a status
# change plus its key movement must be atomic).
# ---------------------------------------------------------------------------

_ORDER_ITEMS = "SELECT id FROM order_items WHERE order_id = ?"


def release_for_order_in(conn: sqlite3.Connection, order_id: int) -> int:
    """reserved -> available for this order's keys. Only 'reserved' keys are
    touched: sold and revoked keys are never released."""
    cur = conn.execute(
        f"""
        UPDATE license_keys
        SET status = 'available', order_item_id = NULL, reserved_until = NULL
        WHERE status = 'reserved' AND order_item_id IN ({_ORDER_ITEMS})
        """,  # noqa: S608 - _ORDER_ITEMS is a fixed constant subquery
        (order_id,),
    )
    return cur.rowcount


def release_for_order(order_id: int) -> int:
    with transaction() as conn:
        return release_for_order_in(conn, order_id)


def sell_reserved_for_order_in(conn: sqlite3.Connection, order_id: int) -> int:
    """reserved -> sold for this order's keys; returns how many were sold."""
    cur = conn.execute(
        f"""
        UPDATE license_keys
        SET status = 'sold', reserved_until = NULL
        WHERE status = 'reserved' AND order_item_id IN ({_ORDER_ITEMS})
        """,  # noqa: S608 - _ORDER_ITEMS is a fixed constant subquery
        (order_id,),
    )
    return cur.rowcount


def count_reserved_for_order(order_id: int) -> int:
    row = (
        get_conn()
        .execute(
            f"""
            SELECT COUNT(*) AS n FROM license_keys
            WHERE status = 'reserved' AND order_item_id IN ({_ORDER_ITEMS})
            """,  # noqa: S608 - _ORDER_ITEMS is a fixed constant subquery
            (order_id,),
        )
        .fetchone()
    )
    return int(row["n"])


def get_sold_keys(order_item_id: int) -> list[str]:
    rows = (
        get_conn()
        .execute(
            """
            SELECT key_value FROM license_keys
            WHERE status = 'sold' AND order_item_id = ?
            ORDER BY id
            """,
            (order_item_id,),
        )
        .fetchall()
    )
    return [row["key_value"] for row in rows]
