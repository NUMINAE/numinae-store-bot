"""Library queries and delivery bookkeeping helpers in repo/orders.py."""

from __future__ import annotations

from bot.repo import orders as orders_repo

from .shop import KEY, TEXT, pay, place_order

USER = 3001
OTHER = 3002


def _stamp_created(db, order_id: int, second: int) -> None:
    db.execute(
        "UPDATE orders SET created_at = ? WHERE id = ?",
        (f"2026-01-01 00:00:{second:02d}", order_id),
    )
    db.commit()


def test_library_lists_paid_delivered_and_refunded_items_newest_first(db):
    paid = place_order(USER, {TEXT: 1})
    pay(paid)
    delivered = place_order(USER, {KEY: 2})
    pay(delivered)
    orders_repo.set_status(delivered, "delivered", "system")
    refunded = place_order(USER, {2: 1})
    pay(refunded)
    orders_repo.set_status(refunded, "refunded", "owner")
    for second, order_id in enumerate((paid, delivered, refunded)):
        _stamp_created(db, order_id, second)

    rows = orders_repo.list_library_items(USER, 10, 0)
    assert [r["order_id"] for r in rows] == [refunded, delivered, paid]
    assert [r["order_status"] for r in rows] == ["refunded", "delivered", "paid"]
    assert orders_repo.count_library_items(USER) == 3


def test_library_excludes_pending_cancelled_and_expired_orders(db):
    pending = place_order(USER, {TEXT: 1})
    assert orders_repo.count_library_items(USER) == 0
    orders_repo.cancel_order(pending, "customer")
    cancelled_stays_out = place_order(USER, {TEXT: 1})
    orders_repo.set_status(cancelled_stays_out, "expired", "system")
    assert orders_repo.list_library_items(USER, 10, 0) == []
    assert orders_repo.count_library_items(USER) == 0


def test_library_is_scoped_to_the_user_and_paged(db):
    for buyer in (USER, OTHER):
        pay(place_order(buyer, {TEXT: 1, 2: 1, KEY: 1}))
    assert orders_repo.count_library_items(USER) == 3
    first = orders_repo.list_library_items(USER, 2, 0)
    rest = orders_repo.list_library_items(USER, 2, 2)
    assert len(first) == 2 and len(rest) == 1
    assert {r["id"] for r in first + rest}.isdisjoint(
        {r["id"] for r in orders_repo.list_library_items(OTHER, 10, 0)}
    )
    assert orders_repo.list_library_items(9999, 10, 0) == []


def test_get_item_with_order_returns_the_owner_and_status_for_any_status(db):
    order_id = place_order(USER, {TEXT: 1})
    (item,) = orders_repo.get_with_items(order_id)[1]
    row = orders_repo.get_item_with_order(item["id"])
    assert row is not None
    assert (row["user_id"], row["order_status"], row["order_id"]) == (
        USER,
        "pending_payment",
        order_id,
    )
    assert row["product_name"] == "Neon Portrait Prompt Pack"
    assert orders_repo.get_item_with_order(999999) is None


def test_delivery_helpers(db):
    order_id = place_order(USER, {TEXT: 1, 2: 1})
    pay(order_id)
    assert [o["id"] for o in orders_repo.list_paid_undelivered_orders()] == [order_id]
    first, second = orders_repo.list_undelivered_items(order_id)

    orders_repo.mark_item_delivered(first["id"])
    assert [i["id"] for i in orders_repo.list_undelivered_items(order_id)] == [second["id"]]
    assert orders_repo.finish_order_if_delivered(order_id) is False
    assert orders_repo.get(order_id)["status"] == "paid"

    stamp = orders_repo.get_item_with_order(first["id"])["delivered_at"]
    orders_repo.mark_item_delivered(first["id"])  # idempotent: keeps the first stamp
    assert orders_repo.get_item_with_order(first["id"])["delivered_at"] == stamp

    orders_repo.mark_item_delivered(second["id"])
    assert orders_repo.finish_order_if_delivered(order_id) is True
    order = orders_repo.get(order_id)
    assert order["status"] == "delivered" and order["delivered_at"] is not None
    assert orders_repo.finish_order_if_delivered(order_id) is False  # already delivered
    assert orders_repo.finish_order_if_delivered(99999) is False
    assert orders_repo.list_paid_undelivered_orders() == []


def test_get_open_for_user_and_list_events(db):
    assert orders_repo.get_open_for_user(USER) is None
    order_id = place_order(USER)
    assert orders_repo.get_open_for_user(USER)["id"] == order_id
    assert orders_repo.get_open_for_user(OTHER) is None
    assert len(orders_repo.list_events(order_id)) == 1
