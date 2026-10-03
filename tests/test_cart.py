from __future__ import annotations

import sqlite3

import pytest

from bot.repo import cart as cart_repo
from bot.repo import products as products_repo
from bot.repo import variants as variants_repo

from .helpers import add_customer

A = 1001
B = 1002
V1 = 1  # 600 cents
V2 = 2  # 1400 cents


@pytest.fixture
def users(db):
    add_customer(A)
    add_customer(B)


def _qty(user_id: int, variant_id: int) -> int | None:
    for line in cart_repo.list_lines(user_id):
        if line["variant_id"] == variant_id:
            return line["quantity"]
    return None


def test_add_merges_quantities(users):
    cart_repo.add(A, V1, 1)
    cart_repo.add(A, V1, 2)
    assert len(cart_repo.list_lines(A)) == 1
    assert _qty(A, V1) == 3


def test_set_qty_replaces_the_quantity(users):
    cart_repo.add(A, V1, 5)
    cart_repo.set_qty(A, V1, 2)
    assert _qty(A, V1) == 2


def test_set_qty_inserts_when_absent(users):
    cart_repo.set_qty(A, V1, 4)
    assert _qty(A, V1) == 4


@pytest.mark.parametrize("qty", [0, -1])
def test_set_qty_nonpositive_removes_the_line(users, qty):
    cart_repo.add(A, V1, 3)
    cart_repo.set_qty(A, V1, qty)
    assert cart_repo.list_lines(A) == []


def test_remove_and_clear(users):
    cart_repo.add(A, V1, 1)
    cart_repo.add(A, V2, 1)
    cart_repo.remove(A, V1)
    assert _qty(A, V1) is None
    assert _qty(A, V2) == 1
    cart_repo.clear(A)
    assert cart_repo.list_lines(A) == []


def test_list_lines_computes_line_totals(users):
    cart_repo.add(A, V2, 3)
    (line,) = cart_repo.list_lines(A)
    assert line["line_total"] == 3 * 1400
    assert line["product_name"] == "Persian Saffron Black Tea"
    assert line["variant_label"] == "250g"


def test_summary_counts_only_available_lines(users):
    cart_repo.add(A, V1, 2)  # 1200
    cart_repo.add(A, V2, 1)  # 1400
    assert cart_repo.summary(A) == (3, 2600)
    variants_repo.set_active(V2, False)
    assert cart_repo.summary(A) == (2, 1200)
    # The unavailable line is still listed, just flagged.
    lines = cart_repo.list_lines(A)
    flags = {ln["variant_id"]: products_repo.is_variant_available(ln) for ln in lines}
    assert flags == {V1: True, V2: False}


def test_summary_of_empty_cart(users):
    assert cart_repo.summary(A) == (0, 0)


def test_zero_quantity_is_rejected_by_the_schema(users):
    with pytest.raises(sqlite3.IntegrityError):
        cart_repo.add(A, V1, 0)


# --- user scoping: every query must be bound to its user_id ----------------


def test_list_lines_is_scoped_to_the_user(users):
    cart_repo.add(A, V1, 1)
    assert cart_repo.list_lines(B) == []
    assert [ln["variant_id"] for ln in cart_repo.list_lines(A)] == [V1]


def test_summary_is_scoped_to_the_user(users):
    cart_repo.add(A, V1, 2)
    assert cart_repo.summary(B) == (0, 0)


def test_add_does_not_touch_another_users_line(users):
    cart_repo.add(A, V1, 1)
    cart_repo.add(B, V1, 5)
    assert _qty(A, V1) == 1
    assert _qty(B, V1) == 5


def test_set_qty_does_not_touch_another_users_line(users):
    cart_repo.add(A, V1, 1)
    cart_repo.set_qty(B, V1, 9)
    cart_repo.set_qty(B, V1, 0)
    assert _qty(A, V1) == 1


def test_remove_does_not_touch_another_users_line(users):
    cart_repo.add(A, V1, 1)
    cart_repo.remove(B, V1)
    assert _qty(A, V1) == 1


def test_clear_does_not_touch_another_users_cart(users):
    cart_repo.add(A, V1, 1)
    cart_repo.add(B, V2, 1)
    cart_repo.clear(B)
    assert _qty(A, V1) == 1
    assert cart_repo.list_lines(B) == []
