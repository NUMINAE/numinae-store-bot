from __future__ import annotations

import sqlite3

import pytest

from bot.db import get_conn
from bot.repo import cart as cart_repo
from bot.repo import categories as categories_repo
from bot.repo import license_keys as keys_repo
from bot.repo import products as products_repo
from bot.repo import variants as variants_repo

from .helpers import add_customer

A = 1001
B = 1002


@pytest.fixture
def shop(empty_db):
    """Customers A and B, and one edition of each delivery kind; the key
    edition has 5 keys in stock."""
    add_customer(A)
    add_customer(B)
    product = products_repo.create(categories_repo.create("C"), "Product")
    text = variants_repo.create(product, "Text", 100, "text")
    variants_repo.set_delivery_text(text, "https://example.com/t")
    file = variants_repo.create(product, "File", 200, "file")
    empty_db.execute("UPDATE product_variants SET delivery_file_id = 'f' WHERE id = ?", (file,))
    empty_db.commit()
    key = variants_repo.create(product, "Key", 300, "key")
    keys_repo.add_many(key, [f"K{n}" for n in range(5)])
    return {"text": text, "file": file, "key": key}


def _qty(user_id: int, variant_id: int) -> int | None:
    for line in cart_repo.list_lines(user_id):
        if line["variant_id"] == variant_id:
            return line["quantity"]
    return None


# --- quantity limits (repo-enforced) ------------------------------------------


@pytest.mark.parametrize("kind", ["text", "file"])
def test_max_quantity_is_one_for_text_and_file(shop, kind):
    assert cart_repo.max_quantity(shop[kind]) == 1


def test_max_quantity_for_keys_is_stock_capped_at_99(shop):
    assert cart_repo.max_quantity(shop["key"]) == 5
    keys_repo.add_many(shop["key"], [f"X{n}" for n in range(200)])
    assert cart_repo.max_quantity(shop["key"]) == 99


def test_max_quantity_of_missing_or_empty_stock_edition_is_zero(shop, empty_db):
    assert cart_repo.max_quantity(99999) == 0
    empty_db.execute("UPDATE license_keys SET status = 'sold'")
    empty_db.commit()
    assert cart_repo.max_quantity(shop["key"]) == 0


@pytest.mark.parametrize("kind", ["text", "file"])
def test_adding_the_same_text_or_file_edition_twice_leaves_quantity_one(shop, kind):
    assert cart_repo.add(A, shop[kind], 1) == 1
    assert cart_repo.add(A, shop[kind], 1) == 1
    assert _qty(A, shop[kind]) == 1


@pytest.mark.parametrize("kind", ["text", "file"])
def test_adding_many_text_or_file_at_once_clamps_to_one(shop, kind):
    assert cart_repo.add(A, shop[kind], 7) == 1
    assert cart_repo.set_qty(A, shop[kind], 9) == 1


def test_key_quantity_clamps_to_stock_via_add_and_set_qty(shop):
    assert cart_repo.add(A, shop["key"], 3) == 3
    assert cart_repo.add(A, shop["key"], 3) == 5
    assert cart_repo.set_qty(A, shop["key"], 50) == 5
    assert _qty(A, shop["key"]) == 5


def test_add_with_no_stock_leaves_the_cart_unchanged(shop, empty_db):
    cart_repo.add(A, shop["key"], 2)
    empty_db.execute("UPDATE license_keys SET status = 'sold'")
    empty_db.commit()
    assert cart_repo.add(A, shop["key"], 1) == 2
    assert _qty(A, shop["key"]) == 2
    assert cart_repo.add(B, shop["key"], 1) == 0
    assert _qty(B, shop["key"]) is None


def test_set_qty_with_no_stock_removes_the_line(shop, empty_db):
    cart_repo.add(A, shop["key"], 2)
    empty_db.execute("UPDATE license_keys SET status = 'sold'")
    empty_db.commit()
    assert cart_repo.set_qty(A, shop["key"], 1) == 0
    assert _qty(A, shop["key"]) is None


def test_add_merges_quantities_for_key_editions(shop):
    cart_repo.add(A, shop["key"], 1)
    cart_repo.add(A, shop["key"], 2)
    assert len(cart_repo.list_lines(A)) == 1
    assert _qty(A, shop["key"]) == 3


def test_set_qty_replaces_and_inserts(shop):
    cart_repo.add(A, shop["key"], 4)
    assert cart_repo.set_qty(A, shop["key"], 2) == 2
    assert cart_repo.set_qty(B, shop["key"], 4) == 4
    assert _qty(A, shop["key"]) == 2


@pytest.mark.parametrize("qty", [0, -1])
def test_set_qty_nonpositive_removes_the_line(shop, qty):
    cart_repo.add(A, shop["key"], 3)
    assert cart_repo.set_qty(A, shop["key"], qty) == 0
    assert cart_repo.list_lines(A) == []


def test_zero_quantity_is_rejected_by_the_schema(shop):
    with pytest.raises(sqlite3.IntegrityError):
        get_conn().execute(
            "INSERT INTO cart_items (user_id, variant_id, quantity) VALUES (?, ?, 0)",
            (A, shop["text"]),
        )


# --- reads -------------------------------------------------------------------


def test_remove_and_clear(shop):
    cart_repo.add(A, shop["text"], 1)
    cart_repo.add(A, shop["key"], 1)
    cart_repo.remove(A, shop["text"])
    assert _qty(A, shop["text"]) is None
    assert _qty(A, shop["key"]) == 1
    cart_repo.clear(A)
    assert cart_repo.list_lines(A) == []


def test_list_lines_computes_line_totals_and_carries_limits(shop):
    cart_repo.add(A, shop["key"], 3)
    (line,) = cart_repo.list_lines(A)
    assert line["line_total"] == 3 * 300
    assert line["price_stars"] == 300
    assert (line["product_name"], line["variant_label"]) == ("Product", "Key")
    assert line["delivery_kind"] == "key"
    assert line["key_stock"] == 5
    assert cart_repo.max_quantity_for(line["delivery_kind"], line["key_stock"]) == 5


def test_summary_counts_only_available_lines(shop):
    cart_repo.add(A, shop["text"], 1)  # 100
    cart_repo.add(A, shop["key"], 2)  # 600
    assert cart_repo.summary(A) == (3, 700)
    variants_repo.set_active(shop["key"], False)
    assert cart_repo.summary(A) == (1, 100)


def test_summary_of_empty_cart(shop):
    assert cart_repo.summary(A) == (0, 0)


# --- user scoping: every query must be bound to its user_id ----------------


def test_reads_are_scoped_to_the_user(shop):
    cart_repo.add(A, shop["text"], 1)
    assert cart_repo.list_lines(B) == []
    assert cart_repo.summary(B) == (0, 0)
    assert [ln["variant_id"] for ln in cart_repo.list_lines(A)] == [shop["text"]]


def test_writes_do_not_touch_another_users_line(shop):
    cart_repo.add(A, shop["key"], 1)
    cart_repo.add(B, shop["key"], 4)
    cart_repo.set_qty(B, shop["key"], 2)
    cart_repo.remove(B, shop["key"])
    assert _qty(A, shop["key"]) == 1
    cart_repo.add(B, shop["text"], 1)
    cart_repo.clear(B)
    assert _qty(A, shop["key"]) == 1
    assert cart_repo.list_lines(B) == []
