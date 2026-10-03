"""Query functions for products and their editions (product_variants).

There is exactly one availability predicate, purchasable_sql(): an edition is
purchasable only if the edition, its product and its category are all active
AND its delivery is configured — a text edition needs delivery text, a file
edition needs an uploaded file, a key edition needs at least one available
licence key. Every list query filters with it and every row that needs to
answer "can this be bought?" (get_variant, repo.cart.list_lines) selects it
as is_purchasable, which is_variant_available() reads. The cascade is
evaluated at read time; a child's own is_active is never rewritten when a
parent is deactivated, so reactivating the parent restores everything.
"""

from __future__ import annotations

import sqlite3

from ..db import get_conn, transaction


def purchasable_sql(v: str = "v", p: str = "p", c: str = "c") -> str:
    """SQL boolean expression over the given edition/product/category aliases.
    The aliases are fixed literals chosen by the callers in this package."""
    return f"""(
        {v}.is_active = 1 AND {p}.is_active = 1 AND {c}.is_active = 1
        AND (
            ({v}.delivery_kind = 'text'
                AND TRIM(COALESCE({v}.delivery_text, ''), ' ' || char(9, 10, 13)) <> '')
            OR ({v}.delivery_kind = 'file' AND COALESCE({v}.delivery_file_id, '') <> '')
            OR ({v}.delivery_kind = 'key' AND EXISTS (
                SELECT 1 FROM license_keys k WHERE k.variant_id = {v}.id AND k.status = 'available'
            ))
        )
    )"""  # noqa: S608 - aliases are fixed literals from callers in this package


def is_variant_available(row: sqlite3.Row) -> bool:
    """Reads the is_purchasable column that get_variant() and
    repo.cart.list_lines() select via purchasable_sql()."""
    return bool(row["is_purchasable"])


def list_active_in_category(category_id: int, limit: int, offset: int) -> list[sqlite3.Row]:
    """Products in the category with at least one purchasable edition, each
    row carrying min_price_stars for the "from ⭐X" list price hint."""
    conn = get_conn()
    return conn.execute(
        f"""
        SELECT p.*,
               (SELECT MIN(v.price_stars) FROM product_variants v
                WHERE v.product_id = p.id AND {purchasable_sql()}) AS min_price_stars
        FROM products p
        JOIN categories c ON c.id = p.category_id
        WHERE p.category_id = ?
          AND EXISTS (
            SELECT 1 FROM product_variants v WHERE v.product_id = p.id AND {purchasable_sql()}
          )
        ORDER BY p.sort_order, p.id
        LIMIT ? OFFSET ?
        """,  # noqa: S608 - purchasable_sql() returns a fixed constant fragment, no user input
        (category_id, limit, offset),
    ).fetchall()


def count_active_in_category(category_id: int) -> int:
    conn = get_conn()
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM products p
        JOIN categories c ON c.id = p.category_id
        WHERE p.category_id = ?
          AND EXISTS (
            SELECT 1 FROM product_variants v WHERE v.product_id = p.id AND {purchasable_sql()}
          )
        """,  # noqa: S608 - purchasable_sql() returns a fixed constant fragment, no user input
        (category_id,),
    ).fetchone()
    return int(row["n"])


def get(product_id: int) -> sqlite3.Row | None:
    """Return the product row regardless of is_active."""
    conn = get_conn()
    return conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()


def list_variants(product_id: int) -> list[sqlite3.Row]:
    """The product's purchasable editions, for customers."""
    conn = get_conn()
    return conn.execute(
        f"""
        SELECT v.*
        FROM product_variants v
        JOIN products p ON p.id = v.product_id
        JOIN categories c ON c.id = p.category_id
        WHERE v.product_id = ? AND {purchasable_sql()}
        ORDER BY v.sort_order, v.id
        """,  # noqa: S608 - purchasable_sql() returns a fixed constant fragment, no user input
        (product_id,),
    ).fetchall()


def get_variant(variant_id: int) -> sqlite3.Row | None:
    """The edition joined with its product and category, regardless of active
    state at any level, plus is_purchasable (the single predicate) and
    key_stock (available licence keys)."""
    conn = get_conn()
    return conn.execute(
        f"""
        SELECT
            v.id               AS variant_id,
            v.label            AS variant_label,
            v.price_stars      AS price_stars,
            v.delivery_kind    AS delivery_kind,
            v.is_active        AS variant_is_active,
            p.id               AS product_id,
            p.name             AS product_name,
            p.description      AS product_description,
            p.is_active        AS product_is_active,
            c.id               AS category_id,
            c.name             AS category_name,
            c.is_active        AS category_is_active,
            (SELECT COUNT(*) FROM license_keys k
             WHERE k.variant_id = v.id AND k.status = 'available') AS key_stock,
            {purchasable_sql()} AS is_purchasable
        FROM product_variants v
        JOIN products p ON p.id = v.product_id
        JOIN categories c ON c.id = p.category_id
        WHERE v.id = ?
        """,  # noqa: S608 - purchasable_sql() returns a fixed constant fragment, no user input
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
    return int(row["c"])


def count_all() -> int:
    conn = get_conn()
    return int(conn.execute("SELECT COUNT(*) AS c FROM products").fetchone()["c"])


def count_variants(product_id: int) -> int:
    """Total editions for the product, including inactive ones."""
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM product_variants WHERE product_id = ?", (product_id,)
    ).fetchone()
    return int(row["c"])


def count_active_variants(product_id: int) -> int:
    """Currently-active edition count — used to detect whether an edition about
    to be deactivated is the product's last one."""
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM product_variants WHERE product_id = ? AND is_active = 1",
        (product_id,),
    ).fetchone()
    return int(row["c"])


def count_purchasable_variants(product_id: int) -> int:
    """Editions of this product purchasable right now, for the deactivation
    blast-radius message."""
    conn = get_conn()
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM product_variants v
        JOIN products p ON p.id = v.product_id
        JOIN categories c ON c.id = p.category_id
        WHERE v.product_id = ? AND {purchasable_sql()}
        """,  # noqa: S608 - purchasable_sql() returns a fixed constant fragment, no user input
        (product_id,),
    ).fetchone()
    return int(row["n"])
