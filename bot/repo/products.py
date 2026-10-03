"""Query functions for products and their weight variants.

is_variant_available() is the single availability predicate — a variant is
purchasable only if the variant, its product, and the product's category are
all active. Every place that decides whether something can be bought (cart
rendering, order creation) evaluates it on a row returned by get_variant() or
repo.cart.list_lines(), which both expose the same
variant_is_active / product_is_active / category_is_active columns.
"""

from __future__ import annotations

import sqlite3

from ..db import get_conn, transaction


def is_variant_available(row: sqlite3.Row) -> bool:
    return (
        bool(row["variant_is_active"])
        and bool(row["product_is_active"])
        and bool(row["category_is_active"])
    )


def list_active_in_category(category_id: int, limit: int, offset: int) -> list[sqlite3.Row]:
    """Products in the category with at least one purchasable variant, each
    row carrying min_price_usd for the "from $X" list price hint."""
    conn = get_conn()
    return conn.execute(
        """
        SELECT p.*,
               (SELECT MIN(v.price_usd) FROM product_variants v
                WHERE v.product_id = p.id AND v.is_active = 1) AS min_price_usd
        FROM products p
        WHERE p.category_id = ?
          AND p.is_active = 1
          AND EXISTS (SELECT 1 FROM categories c WHERE c.id = p.category_id AND c.is_active = 1)
          AND EXISTS (
            SELECT 1 FROM product_variants v WHERE v.product_id = p.id AND v.is_active = 1
          )
        ORDER BY p.sort_order, p.id
        LIMIT ? OFFSET ?
        """,
        (category_id, limit, offset),
    ).fetchall()


def count_active_in_category(category_id: int) -> int:
    conn = get_conn()
    row = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM products p
        WHERE p.category_id = ?
          AND p.is_active = 1
          AND EXISTS (SELECT 1 FROM categories c2 WHERE c2.id = p.category_id AND c2.is_active = 1)
          AND EXISTS (
            SELECT 1 FROM product_variants v WHERE v.product_id = p.id AND v.is_active = 1
          )
        """,
        (category_id,),
    ).fetchone()
    return row["c"]


def get(product_id: int) -> sqlite3.Row | None:
    """Return the product row regardless of is_active."""
    conn = get_conn()
    return conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()


def list_variants(product_id: int) -> list[sqlite3.Row]:
    conn = get_conn()
    return conn.execute(
        """
        SELECT * FROM product_variants
        WHERE product_id = ? AND is_active = 1
        ORDER BY sort_order, id
        """,
        (product_id,),
    ).fetchall()


def get_variant(variant_id: int) -> sqlite3.Row | None:
    """Return the variant joined with its product and category, regardless of
    active state at any level — enough to render a cart line and to evaluate
    is_variant_available()."""
    conn = get_conn()
    return conn.execute(
        """
        SELECT
            v.id          AS variant_id,
            v.label       AS variant_label,
            v.price_usd   AS price_usd,
            v.is_active   AS variant_is_active,
            p.id          AS product_id,
            p.name        AS product_name,
            p.description AS product_description,
            p.is_active   AS product_is_active,
            c.id          AS category_id,
            c.name        AS category_name,
            c.is_active   AS category_is_active
        FROM product_variants v
        JOIN products p ON p.id = v.product_id
        JOIN categories c ON c.id = p.category_id
        WHERE v.id = ?
        """,
        (variant_id,),
    ).fetchone()


# ---------------------------------------------------------------------------
# Admin — these always include inactive rows. Kept separate from the
# customer-facing functions above (rather than adding an include_inactive
# flag to them) so the customer path can never accidentally show a
# deactivated product.
# ---------------------------------------------------------------------------


def create(category_id: int, name: str) -> int:
    with transaction() as conn:
        next_sort = conn.execute(
            "SELECT COALESCE(MAX(sort_order), -10) + 10 AS n FROM products WHERE category_id = ?",
            (category_id,),
        ).fetchone()["n"]
        cur = conn.execute(
            """
            INSERT INTO products (category_id, name, description, is_active, sort_order)
            VALUES (?, ?, '', 1, ?)
            """,
            (category_id, name, next_sort),
        )
        return cur.lastrowid  # type: ignore[return-value]  # lastrowid is always set after an INSERT


def update_name(product_id: int, name: str) -> None:
    with transaction() as conn:
        conn.execute(
            "UPDATE products SET name = ?, updated_at = datetime('now') WHERE id = ?",
            (name, product_id),
        )


def update_description(product_id: int, description: str) -> None:
    with transaction() as conn:
        conn.execute(
            "UPDATE products SET description = ?, updated_at = datetime('now') WHERE id = ?",
            (description, product_id),
        )


def update_sort_order(product_id: int, sort_order: int) -> None:
    with transaction() as conn:
        conn.execute(
            "UPDATE products SET sort_order = ?, updated_at = datetime('now') WHERE id = ?",
            (sort_order, product_id),
        )


def update_category(product_id: int, category_id: int) -> None:
    with transaction() as conn:
        conn.execute(
            "UPDATE products SET category_id = ?, updated_at = datetime('now') WHERE id = ?",
            (category_id, product_id),
        )


def set_active(product_id: int, is_active: bool) -> None:
    with transaction() as conn:
        conn.execute(
            "UPDATE products SET is_active = ?, updated_at = datetime('now') WHERE id = ?",
            (1 if is_active else 0, product_id),
        )


def list_all_in_category(category_id: int, limit: int, offset: int) -> list[sqlite3.Row]:
    """All products in the category including inactive, for the admin list."""
    conn = get_conn()
    return conn.execute(
        """
        SELECT * FROM products
        WHERE category_id = ?
        ORDER BY sort_order, id
        LIMIT ? OFFSET ?
        """,
        (category_id, limit, offset),
    ).fetchall()


def count_all_in_category(category_id: int) -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM products WHERE category_id = ?", (category_id,)
    ).fetchone()
    return row["c"]


def count_all() -> int:
    conn = get_conn()
    return conn.execute("SELECT COUNT(*) AS c FROM products").fetchone()["c"]


def count_variants(product_id: int) -> int:
    """Total variants for the product, including inactive ones."""
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM product_variants WHERE product_id = ?", (product_id,)
    ).fetchone()
    return row["c"]


def count_active_variants(product_id: int) -> int:
    """Currently-active variant count — used to detect whether a variant about
    to be deactivated is the product's last one."""
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM product_variants WHERE product_id = ? AND is_active = 1",
        (product_id,),
    ).fetchone()
    return row["c"]
