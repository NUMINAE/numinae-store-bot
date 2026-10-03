"""Query functions for cart_items, keyed on variant_id.

Reads use LEFT JOINs against product_variants/products/categories so a line
would still render even if one of those rows were ever missing — in practice
none of the three are ever hard-deleted (they deactivate via is_active
instead), but the LEFT JOIN keeps this module correct regardless.
"""

from __future__ import annotations

import sqlite3

from ..db import get_conn, transaction
from . import products as products_repo


def add(user_id: int, variant_id: int, qty: int) -> None:
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO cart_items (user_id, variant_id, quantity)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, variant_id) DO UPDATE SET
                quantity = quantity + excluded.quantity
            """,
            (user_id, variant_id, qty),
        )


def set_qty(user_id: int, variant_id: int, qty: int) -> None:
    with transaction() as conn:
        if qty <= 0:
            conn.execute(
                "DELETE FROM cart_items WHERE user_id = ? AND variant_id = ?",
                (user_id, variant_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO cart_items (user_id, variant_id, quantity)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id, variant_id) DO UPDATE SET
                    quantity = excluded.quantity
                """,
                (user_id, variant_id, qty),
            )


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
        """
        SELECT
            ci.variant_id   AS variant_id,
            ci.quantity     AS quantity,
            p.name          AS product_name,
            v.label         AS variant_label,
            v.price_usd     AS price_usd,
            v.is_active     AS variant_is_active,
            p.is_active     AS product_is_active,
            c.is_active     AS category_is_active,
            COALESCE(v.price_usd * ci.quantity, 0) AS line_total
        FROM cart_items ci
        LEFT JOIN product_variants v ON v.id = ci.variant_id
        LEFT JOIN products p ON p.id = v.product_id
        LEFT JOIN categories c ON c.id = p.category_id
        WHERE ci.user_id = ?
        ORDER BY ci.added_at
        """,
        (user_id,),
    ).fetchall()


def summary(user_id: int) -> tuple[int, int]:
    """(item_count, subtotal_cents) counting only available lines — reuses
    is_variant_available() rather than re-deriving the three-way check."""
    lines = list_lines(user_id)
    available = [line for line in lines if products_repo.is_variant_available(line)]
    item_count = sum(line["quantity"] for line in available)
    subtotal_cents = sum(line["line_total"] for line in available)
    return item_count, subtotal_cents
