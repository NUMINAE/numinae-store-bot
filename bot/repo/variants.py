"""Query functions for product_variants, for the admin catalogue manager.

Customer-facing variant reads live in repo/products.py (list_variants,
get_variant, is_variant_available); this module is admin-only and its
listing function always includes inactive rows.
"""

from __future__ import annotations

import sqlite3

from ..db import get_conn, transaction


def create(product_id: int, label: str, price_usd: int) -> int:
    with transaction() as conn:
        next_sort = conn.execute(
            "SELECT COALESCE(MAX(sort_order), -10) + 10 AS n FROM product_variants WHERE product_id = ?",  # noqa: E501
            (product_id,),
        ).fetchone()["n"]
        cur = conn.execute(
            """
            INSERT INTO product_variants (product_id, label, price_usd, is_active, sort_order)
            VALUES (?, ?, ?, 1, ?)
            """,
            (product_id, label, price_usd, next_sort),
        )
        return cur.lastrowid  # type: ignore[return-value]  # lastrowid is always set after an INSERT


def update_label(variant_id: int, label: str) -> None:
    with transaction() as conn:
        conn.execute(
            "UPDATE product_variants SET label = ?, updated_at = datetime('now') WHERE id = ?",
            (label, variant_id),
        )


def update_price(variant_id: int, price_usd: int) -> None:
    with transaction() as conn:
        conn.execute(
            "UPDATE product_variants SET price_usd = ?, updated_at = datetime('now') WHERE id = ?",
            (price_usd, variant_id),
        )


def update_sort_order(variant_id: int, sort_order: int) -> None:
    with transaction() as conn:
        conn.execute(
            "UPDATE product_variants SET sort_order = ?, updated_at = datetime('now') WHERE id = ?",
            (sort_order, variant_id),
        )


def set_active(variant_id: int, is_active: bool) -> None:
    with transaction() as conn:
        conn.execute(
            "UPDATE product_variants SET is_active = ?, updated_at = datetime('now') WHERE id = ?",
            (1 if is_active else 0, variant_id),
        )


def list_all_for_product(product_id: int) -> list[sqlite3.Row]:
    """All variants for the product including inactive, ordered by sort_order."""
    conn = get_conn()
    return conn.execute(
        """
        SELECT * FROM product_variants
        WHERE product_id = ?
        ORDER BY sort_order, id
        """,
        (product_id,),
    ).fetchall()


def get(variant_id: int) -> sqlite3.Row | None:
    conn = get_conn()
    return conn.execute("SELECT * FROM product_variants WHERE id = ?", (variant_id,)).fetchone()
