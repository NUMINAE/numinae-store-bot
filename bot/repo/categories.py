"""Query functions for the categories table."""

from __future__ import annotations

import sqlite3

from ..db import get_conn, transaction
from .products import purchasable_sql


def list_active() -> list[sqlite3.Row]:
    """Active categories that contain at least one purchasable edition, so a
    customer never opens an empty category."""
    conn = get_conn()
    return conn.execute(
        f"""
        SELECT * FROM categories c
        WHERE EXISTS (
            SELECT 1 FROM products p
            JOIN product_variants v ON v.product_id = p.id
            WHERE p.category_id = c.id AND {purchasable_sql()}
        )
        ORDER BY c.sort_order, c.id
        """  # noqa: S608 - purchasable_sql() returns a fixed constant fragment, no user input
    ).fetchall()


def get(category_id: int) -> sqlite3.Row | None:
    conn = get_conn()
    return conn.execute("SELECT * FROM categories WHERE id = ?", (category_id,)).fetchone()


# ---------------------------------------------------------------------------
# Admin — these always include inactive rows. Kept separate from the
# customer-facing functions above (rather than adding an include_inactive
# flag to them) so the customer path can never accidentally show a
# deactivated category.
# ---------------------------------------------------------------------------


def create(name: str) -> int:
    with transaction() as conn:
        next_sort = conn.execute(
            "SELECT COALESCE(MAX(sort_order), -10) + 10 AS n FROM categories"
        ).fetchone()["n"]
        cur = conn.execute(
            "INSERT INTO categories (name, description, is_active, sort_order)"
            " VALUES (?, '', 1, ?)",
            (name, next_sort),
        )
        return cur.lastrowid  # type: ignore[return-value]  # lastrowid is always set after an INSERT


def update_name(category_id: int, name: str) -> None:
    with transaction() as conn:
        conn.execute(
            "UPDATE categories SET name = ?, updated_at = datetime('now') WHERE id = ?",
            (name, category_id),
        )


def update_description(category_id: int, description: str) -> None:
    with transaction() as conn:
        conn.execute(
            "UPDATE categories SET description = ?, updated_at = datetime('now') WHERE id = ?",
            (description, category_id),
        )


def update_sort_order(category_id: int, sort_order: int) -> None:
    with transaction() as conn:
        conn.execute(
            "UPDATE categories SET sort_order = ?, updated_at = datetime('now') WHERE id = ?",
            (sort_order, category_id),
        )


def set_active(category_id: int, is_active: bool) -> None:
    with transaction() as conn:
        conn.execute(
            "UPDATE categories SET is_active = ?, updated_at = datetime('now') WHERE id = ?",
            (1 if is_active else 0, category_id),
        )


def list_all() -> list[sqlite3.Row]:
    """All categories including inactive, for the admin catalogue root."""
    conn = get_conn()
    return conn.execute("SELECT * FROM categories ORDER BY sort_order, id").fetchall()


def count_all() -> int:
    conn = get_conn()
    return conn.execute("SELECT COUNT(*) AS c FROM categories").fetchone()["c"]


def count_products(category_id: int) -> int:
    """Total products in the category, including inactive ones."""
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM products WHERE category_id = ?", (category_id,)
    ).fetchone()
    return row["c"]


def count_purchasable_variants(category_id: int) -> int:
    """Editions purchasable right now in this category, for the deactivation
    blast-radius message."""
    conn = get_conn()
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS c
        FROM product_variants v
        JOIN products p ON p.id = v.product_id
        JOIN categories c ON c.id = p.category_id
        WHERE p.category_id = ? AND {purchasable_sql()}
        """,  # noqa: S608 - purchasable_sql() returns a fixed constant fragment, no user input
        (category_id,),
    ).fetchone()
    return int(row["c"])
