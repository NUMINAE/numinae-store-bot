"""Query functions for product_variants, for the admin catalogue manager.

Customer-facing edition reads live in repo/products.py (list_variants,
get_variant, is_variant_available); this module is admin-only and its
listing function always includes inactive rows.
"""

from __future__ import annotations

import sqlite3

from ..db import get_conn, transaction

DELIVERY_KINDS = ("file", "key", "text")


def create(product_id: int, label: str, price_stars: int, delivery_kind: str) -> int:
    """New editions start active. delivery_kind is fixed from here on — there
    is deliberately no update function for it."""
    if delivery_kind not in DELIVERY_KINDS:
        raise ValueError(f"Unknown delivery kind {delivery_kind!r}.")
    with transaction() as conn:
        next_sort = conn.execute(
            "SELECT COALESCE(MAX(sort_order), -10) + 10 AS n"
            " FROM product_variants WHERE product_id = ?",
            (product_id,),
        ).fetchone()["n"]
        cur = conn.execute(
            """
            INSERT INTO product_variants
                (product_id, label, price_stars, delivery_kind, is_active, sort_order)
            VALUES (?, ?, ?, ?, 1, ?)
            """,
            (product_id, label, price_stars, delivery_kind, next_sort),
        )
        return cur.lastrowid  # type: ignore[return-value]  # lastrowid is always set after an INSERT


def set_delivery_file(variant_id: int, file_id: str, file_name: str | None) -> None:
    """Store the uploaded file's Telegram file_id and original name. Only file
    editions take a file; replacing an existing one is allowed (existing buyers
    then re-download the new file)."""
    with transaction() as conn:
        row = conn.execute(
            "SELECT delivery_kind FROM product_variants WHERE id = ?", (variant_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Edition {variant_id} does not exist.")
        if row["delivery_kind"] != "file":
            raise ValueError("Only file editions take an uploaded file.")
        conn.execute(
            "UPDATE product_variants SET delivery_file_id = ?, delivery_file_name = ?,"
            " updated_at = datetime('now') WHERE id = ?",
            (file_id, file_name, variant_id),
        )


def update_label(variant_id: int, label: str) -> None:
    with transaction() as conn:
        conn.execute(
            "UPDATE product_variants SET label = ?, updated_at = datetime('now') WHERE id = ?",
            (label, variant_id),
        )


def update_price(variant_id: int, price_stars: int) -> None:
    with transaction() as conn:
        conn.execute(
            "UPDATE product_variants SET price_stars = ?, updated_at = datetime('now') "
            "WHERE id = ?",
            (price_stars, variant_id),
        )


def set_delivery_text(variant_id: int, text: str) -> None:
    """Stored raw; escaped only at render time."""
    with transaction() as conn:
        conn.execute(
            "UPDATE product_variants SET delivery_text = ?, updated_at = datetime('now') "
            "WHERE id = ?",
            (text, variant_id),
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
