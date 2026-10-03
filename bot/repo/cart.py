"""Query functions for cart_items, keyed on variant_id.

Quantity limits are enforced here, not only in the UI: file and text editions
are one-per-customer (max_quantity 1), key editions are capped at the number
of available licence keys (and 99). add() and set_qty() clamp to that limit
and return the quantity the cart line ends up with.

Reads use LEFT JOINs against product_variants/products/categories so a line
would still render even if one of those rows were ever missing — in practice
none of the three are ever hard-deleted (they deactivate via is_active
instead), but the LEFT JOIN keeps this module correct regardless.
"""

from __future__ import annotations

import sqlite3

from ..db import get_conn, transaction
from . import products as products_repo

MAX_KEY_QUANTITY = 99


def max_quantity_for(delivery_kind: str, key_stock: int) -> int:
    """The per-line limit given an edition's delivery kind and available keys."""
    if delivery_kind == "key":
        return max(0, min(MAX_KEY_QUANTITY, key_stock))
    return 1


def max_quantity(variant_id: int) -> int:
    """Limit for this edition; 0 if it doesn't exist or is a key edition
    with no stock."""
    variant = products_repo.get_variant(variant_id)
    if variant is None:
        return 0
    return max_quantity_for(variant["delivery_kind"], variant["key_stock"])


def current_qty(user_id: int, variant_id: int) -> int:
    row = (
        get_conn()
        .execute(
            "SELECT quantity FROM cart_items WHERE user_id = ? AND variant_id = ?",
            (user_id, variant_id),
        )
        .fetchone()
    )
    return int(row["quantity"]) if row else 0


def add(user_id: int, variant_id: int, qty: int) -> int:
    """Add qty to the line, clamped to max_quantity(). Returns the line's
    effective quantity. If the limit is 0 the cart is left unchanged."""
    limit = max_quantity(variant_id)
    existing = current_qty(user_id, variant_id)
    if limit <= 0:
        return existing
    effective = min(existing + qty, limit)
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO cart_items (user_id, variant_id, quantity)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, variant_id) DO UPDATE SET quantity = excluded.quantity
            """,
            (user_id, variant_id, effective),
        )
    return effective


def set_qty(user_id: int, variant_id: int, qty: int) -> int:
    """Set the line's quantity, clamped to max_quantity(). A target of zero
    or less — or a limit of zero — removes the line. Returns the effective
    quantity (0 when the line is gone)."""
    effective = min(qty, max_quantity(variant_id))
    with transaction() as conn:
        if effective <= 0:
            conn.execute(
                "DELETE FROM cart_items WHERE user_id = ? AND variant_id = ?",
                (user_id, variant_id),
            )
            return 0
        conn.execute(
            """
            INSERT INTO cart_items (user_id, variant_id, quantity)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, variant_id) DO UPDATE SET quantity = excluded.quantity
            """,
            (user_id, variant_id, effective),
        )
    return effective


def remove(user_id: int, variant_id: int) -> None:
    with transaction() as conn:
        conn.execute(
            "DELETE FROM cart_items WHERE user_id = ? AND variant_id = ?",
            (user_id, variant_id),
        )


def clear(user_id: int) -> None:
    with transaction() as conn:
        conn.execute("DELETE FROM cart_items WHERE user_id = ?", (user_id,))


def list_lines(user_id: int) -> list[sqlite3.Row]:
    conn = get_conn()
    return conn.execute(
        f"""
        SELECT
            ci.variant_id   AS variant_id,
            ci.quantity     AS quantity,
            p.name          AS product_name,
            v.label         AS variant_label,
            v.price_stars   AS price_stars,
            v.delivery_kind AS delivery_kind,
            (SELECT COUNT(*) FROM license_keys k
             WHERE k.variant_id = v.id AND k.status = 'available') AS key_stock,
            COALESCE({products_repo.purchasable_sql()}, 0) AS is_purchasable,
            COALESCE(v.price_stars * ci.quantity, 0) AS line_total
        FROM cart_items ci
        LEFT JOIN product_variants v ON v.id = ci.variant_id
        LEFT JOIN products p ON p.id = v.product_id
        LEFT JOIN categories c ON c.id = p.category_id
        WHERE ci.user_id = ?
        ORDER BY ci.added_at, ci.variant_id
        """,  # noqa: S608 - purchasable_sql() returns a fixed constant fragment, no user input
        (user_id,),
    ).fetchall()


def summary(user_id: int) -> tuple[int, int]:
    """(item_count, subtotal_stars) counting only available lines — reuses
    is_variant_available() rather than re-deriving the check."""
    lines = list_lines(user_id)
    available = [line for line in lines if products_repo.is_variant_available(line)]
    item_count = sum(line["quantity"] for line in available)
    subtotal = sum(line["line_total"] for line in available)
    return item_count, subtotal
