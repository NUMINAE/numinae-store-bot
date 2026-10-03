"""Order status machine, event log and the schema's order invariants."""

from __future__ import annotations

import itertools
import sqlite3

import pytest

from bot.repo import orders as orders_repo
from bot.repo.orders import ALLOWED_TRANSITIONS, IllegalTransitionError, OrderError

from .helpers import add_customer

STATUSES = ("pending_payment", "paid", "delivered", "cancelled", "expired", "refunded")
_next_user = itertools.count(5000)


def make_order(conn, status: str = "pending_payment", user_id: int | None = None) -> int:
    if user_id is None:
        user_id = next(_next_user)
    add_customer(user_id)
    cur = conn.execute(
        "INSERT INTO orders (user_id, total_stars, item_count, payment_mode, status)"
        " VALUES (?, 100, 1, 'mock', ?)",
        (user_id, status),
    )
    conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


def events(conn, order_id: int) -> list[tuple]:
    return [
        tuple(r)
        for r in conn.execute(
            "SELECT from_status, to_status, actor, note FROM order_events"
            " WHERE order_id = ? ORDER BY id",
            (order_id,),
        )
    ]


# --- transition table --------------------------------------------------------


def test_transition_table_is_exactly_the_specified_graph():
    assert {
        "pending_payment": {"paid", "cancelled", "expired"},
        "paid": {"delivered", "refunded"},
        "delivered": {"refunded"},
        "cancelled": set(),
        "expired": set(),
        "refunded": set(),
    } == ALLOWED_TRANSITIONS
    assert set(ALLOWED_TRANSITIONS) == set(STATUSES)


@pytest.mark.parametrize(("src", "dst"), list(itertools.product(STATUSES, STATUSES)))
def test_every_status_pair(db, src, dst):
    order_id = make_order(db, src)
    if dst in ALLOWED_TRANSITIONS[src]:
        orders_repo.set_status(order_id, dst, "system", note="n")
        assert orders_repo.get(order_id)["status"] == dst
        assert events(db, order_id) == [(src, dst, "system", "n")]
    else:
        with pytest.raises(IllegalTransitionError) as info:
            orders_repo.set_status(order_id, dst, "system")
        assert (info.value.from_status, info.value.to_status) == (src, dst)
        assert orders_repo.get(order_id)["status"] == src
        assert events(db, order_id) == []  # nothing half-written


def test_unknown_target_status_is_illegal(db):
    order_id = make_order(db)
    with pytest.raises(IllegalTransitionError):
        orders_repo.set_status(order_id, "shipped", "system")


def test_missing_order_raises(db):
    with pytest.raises(OrderError, match="not found"):
        orders_repo.set_status(424242, "paid", "system")


def test_a_full_lifecycle_logs_every_step_in_order(db):
    order_id = make_order(db)
    orders_repo.set_status(order_id, "paid", "customer")
    orders_repo.set_status(order_id, "delivered", "system")
    orders_repo.set_status(order_id, "refunded", "owner", note="goodwill")
    assert events(db, order_id) == [
        ("pending_payment", "paid", "customer", None),
        ("paid", "delivered", "system", None),
        ("delivered", "refunded", "owner", "goodwill"),
    ]


def test_status_timestamps_are_stamped_on_entry(db):
    order_id = make_order(db)
    row = orders_repo.get(order_id)
    assert (row["paid_at"], row["delivered_at"], row["refunded_at"]) == (None, None, None)
    orders_repo.set_status(order_id, "paid", "system")
    assert orders_repo.get(order_id)["paid_at"] is not None
    assert orders_repo.get(order_id)["delivered_at"] is None
    orders_repo.set_status(order_id, "delivered", "system")
    orders_repo.set_status(order_id, "refunded", "system")
    row = orders_repo.get(order_id)
    assert row["delivered_at"] is not None
    assert row["refunded_at"] is not None


def test_cancel_has_no_extra_timestamp(db):
    order_id = make_order(db)
    orders_repo.set_status(order_id, "cancelled", "customer")
    row = orders_repo.get(order_id)
    assert (row["paid_at"], row["delivered_at"], row["refunded_at"]) == (None, None, None)


# --- reads -------------------------------------------------------------------


def test_get_missing_is_none(db):
    assert orders_repo.get(999) is None
    assert orders_repo.get_with_items(999) == (None, [])


def test_get_with_items_returns_lines_in_order(db):
    order_id = make_order(db)
    for name in ("First", "Second"):
        db.execute(
            "INSERT INTO order_items (order_id, product_name, variant_label, delivery_kind,"
            " unit_price_stars, quantity, line_total_stars) VALUES (?, ?, 'v', 'text', 10, 2, 20)",
            (order_id, name),
        )
    db.commit()
    order, items = orders_repo.get_with_items(order_id)
    assert order["id"] == order_id
    assert [i["product_name"] for i in items] == ["First", "Second"]


def test_list_for_user_is_scoped_newest_first_and_paged(db):
    user, other = 7001, 7002
    ids = [make_order(db, "cancelled", user) for _ in range(3)]
    make_order(db, "cancelled", other)
    for offset, order_id in enumerate(ids):
        db.execute(
            "UPDATE orders SET created_at = ? WHERE id = ?",
            (f"2026-01-01 00:00:0{offset}", order_id),
        )
    db.commit()
    assert [r["id"] for r in orders_repo.list_for_user(user, 10, 0)] == ids[::-1]
    assert [r["id"] for r in orders_repo.list_for_user(user, 2, 2)] == [ids[0]]
    assert orders_repo.list_for_user(9999, 10, 0) == []


# --- schema invariants -------------------------------------------------------


def test_one_pending_order_per_customer(db):
    make_order(db, "pending_payment", 8001)
    with pytest.raises(sqlite3.IntegrityError):
        make_order(db, "pending_payment", 8001)


def test_a_new_pending_order_is_allowed_once_the_old_one_leaves_pending(db):
    first = make_order(db, "pending_payment", 8002)
    orders_repo.set_status(first, "cancelled", "customer")
    make_order(db, "pending_payment", 8002)


def test_only_pending_is_limited_to_one(db):
    make_order(db, "paid", 8003)
    make_order(db, "paid", 8003)
    make_order(db, "pending_payment", 8003)


def test_different_customers_can_each_have_a_pending_order(db):
    make_order(db, "pending_payment", 8004)
    make_order(db, "pending_payment", 8005)


def test_charge_id_is_unique_when_present_and_repeatable_when_null(db):
    first, second = make_order(db, "paid"), make_order(db, "paid")
    db.execute("UPDATE orders SET telegram_payment_charge_id = 'ch_1' WHERE id = ?", (first,))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE orders SET telegram_payment_charge_id = 'ch_1' WHERE id = ?", (second,))
    db.rollback()
    third = make_order(db, "paid")  # two NULL charge ids coexist
    assert third != second


@pytest.mark.parametrize(
    ("column", "value"),
    [("total_stars", 0), ("payment_mode", "'invoice'"), ("status", "'awaiting_payment'")],
)
def test_check_constraints(db, column, value):
    order_id = make_order(db)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(f"UPDATE orders SET {column} = {value} WHERE id = ?", (order_id,))  # noqa: S608 - column/value come from the parametrize table
    db.rollback()


def test_order_items_keep_snapshots_when_a_variant_goes_and_cascade_with_the_order(db):
    order_id = make_order(db)
    db.execute(
        "INSERT INTO order_items (order_id, variant_id, product_name, variant_label,"
        " delivery_kind, unit_price_stars, quantity, line_total_stars)"
        " VALUES (?, 1, 'p', 'v', 'text', 10, 1, 10)",
        (order_id,),
    )
    db.execute("DELETE FROM product_variants WHERE id = 1")  # ON DELETE SET NULL
    assert db.execute("SELECT variant_id FROM order_items").fetchone()[0] is None
    db.execute("DELETE FROM orders WHERE id = ?", (order_id,))
    assert db.execute("SELECT COUNT(*) FROM order_items").fetchone()[0] == 0
