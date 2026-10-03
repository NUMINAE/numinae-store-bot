"""R3 repo additions: key counts and revocation, delivery files, and the admin
order queries (listing, filters, card, mark_refunded)."""

from __future__ import annotations

import sqlite3

import pytest

from bot.repo import categories as categories_repo
from bot.repo import customers as customers_repo
from bot.repo import license_keys as keys_repo
from bot.repo import orders as orders_repo
from bot.repo import products as products_repo
from bot.repo import variants as variants_repo
from bot.repo.orders import IllegalTransitionError

from .shop import KEY, TEXT, key_states, pay, place_order

USER = 3001
OTHER = 3002


def make_variant(kind: str) -> int:
    product = products_repo.create(categories_repo.create("C"), "P")
    return variants_repo.create(product, "V", 10, kind)


# --- key counts -------------------------------------------------------------------------------


def test_count_by_status_is_zero_filled(empty_db):
    variant = make_variant("key")
    assert keys_repo.count_by_status(variant) == {
        "available": 0,
        "reserved": 0,
        "sold": 0,
        "revoked": 0,
    }


def test_count_by_status_counts_each_status_per_edition(db):
    place_order(USER, {KEY: 3})
    pay(orders_repo.get_open_for_user(USER)["id"])
    place_order(OTHER, {KEY: 2})
    db.execute(
        "UPDATE license_keys SET status = 'revoked' WHERE id ="
        " (SELECT MAX(id) FROM license_keys WHERE variant_id = ? AND status = 'available')",
        (KEY,),
    )
    db.commit()
    assert keys_repo.count_by_status(KEY) == {
        "available": 4,
        "reserved": 2,
        "sold": 3,
        "revoked": 1,
    }
    assert keys_repo.count_by_status(8)["available"] == 10  # another edition is separate


def test_add_many_reports_duplicates_against_existing_stock_by_arithmetic(empty_db):
    variant = make_variant("key")
    keys_repo.add_many(variant, ["A", "B"])
    batch = ["A", "B", "C", "D"]
    added = keys_repo.add_many(variant, batch)
    assert added == 2 and len(batch) - added == 2  # the two already in stock
    assert keys_repo.count_by_status(variant)["available"] == 4


# --- revoke_for_order -------------------------------------------------------------------------


def test_revoke_for_order_touches_only_sold_keys_of_that_order(db):
    paid = place_order(USER, {KEY: 3})
    pay(paid)
    reserved = place_order(OTHER, {KEY: 2})  # reserved, not sold
    other_available = keys_repo.count_available(KEY)

    assert keys_repo.revoke_for_order(paid) == 3
    assert key_states(db) == {"available": other_available, "reserved": 2, "revoked": 3}
    assert keys_repo.revoke_for_order(paid) == 0  # nothing left to revoke
    assert keys_repo.revoke_for_order(reserved) == 0  # reserved keys are not touched
    assert keys_repo.count_by_status(KEY)["reserved"] == 2


def test_revoked_keys_are_never_claimable_again(db):
    order_id = place_order(USER, {KEY: 10})
    pay(order_id)
    keys_repo.revoke_for_order(order_id)
    assert keys_repo.count_available(KEY) == 0
    with pytest.raises(orders_repo.EmptyCartError):
        place_order(OTHER, {KEY: 1})


# --- set_delivery_file ------------------------------------------------------------------------


def test_set_delivery_file_stores_id_and_name_and_allows_replacing(empty_db):
    variant = make_variant("file")
    variants_repo.set_delivery_file(variant, "FILE_A", "a.zip")
    row = variants_repo.get(variant)
    assert (row["delivery_file_id"], row["delivery_file_name"]) == ("FILE_A", "a.zip")
    variants_repo.set_delivery_file(variant, "FILE_B", None)
    row = variants_repo.get(variant)
    assert (row["delivery_file_id"], row["delivery_file_name"]) == ("FILE_B", None)


@pytest.mark.parametrize("kind", ["text", "key"])
def test_set_delivery_file_refuses_other_kinds(empty_db, kind):
    variant = make_variant(kind)
    with pytest.raises(ValueError, match="file editions"):
        variants_repo.set_delivery_file(variant, "F", "f.zip")
    assert variants_repo.get(variant)["delivery_file_id"] is None


def test_set_delivery_file_refuses_a_missing_edition(empty_db):
    with pytest.raises(ValueError, match="does not exist"):
        variants_repo.set_delivery_file(99999, "F", "f.zip")


def test_a_file_edition_becomes_purchasable_once_it_has_a_file(empty_db):
    variant = make_variant("file")
    assert not products_repo.is_variant_available(products_repo.get_variant(variant))
    variants_repo.set_delivery_file(variant, "F", "f.zip")
    assert products_repo.is_variant_available(products_repo.get_variant(variant))


# --- admin order queries ----------------------------------------------------------------------


def build_orders(db):
    """pending (3001), paid-undelivered (3002), delivered, refunded, cancelled, expired."""
    ids = {}
    ids["pending"] = place_order(3001, {TEXT: 1})
    ids["paid"] = place_order(3002, {TEXT: 1})
    pay(ids["paid"])
    ids["delivered"] = place_order(3003, {TEXT: 1})
    pay(ids["delivered"])
    orders_repo.set_status(ids["delivered"], "delivered", "system")
    ids["refunded"] = place_order(3004, {TEXT: 1})
    pay(ids["refunded"])
    orders_repo.mark_refunded(ids["refunded"], "owner")
    ids["cancelled"] = place_order(3005, {TEXT: 1})
    orders_repo.cancel_order(ids["cancelled"], "customer")
    ids["expired"] = place_order(3006, {TEXT: 1})
    orders_repo.set_status(ids["expired"], "expired", "system")
    for n, order_id in enumerate(ids.values()):
        db.execute(
            "UPDATE orders SET created_at = ? WHERE id = ?", (f"2026-01-01 00:00:0{n}", order_id)
        )
    db.commit()
    return ids


@pytest.mark.parametrize(
    ("filter_key", "expected"),
    [
        ("all", {"pending", "paid", "delivered", "refunded", "cancelled", "expired"}),
        ("pending_payment", {"pending"}),
        ("paid", {"paid"}),
        ("delivered", {"delivered"}),
        ("refunded", {"refunded"}),
        ("closed", {"cancelled", "expired"}),
        ("attention", {"paid"}),
    ],
)
def test_each_filter_selects_the_right_orders(db, filter_key, expected):
    ids = build_orders(db)
    rows = orders_repo.list_for_admin(filter_key, 50, 0)
    assert {r["id"] for r in rows} == {ids[name] for name in expected}
    assert orders_repo.count_for_admin(filter_key) == len(expected)


def test_attention_is_paid_orders_with_an_undelivered_item(db):
    order_id = place_order(USER, {TEXT: 1, 2: 1})
    pay(order_id)
    assert orders_repo.attention_count() == 1
    first, second = orders_repo.list_undelivered_items(order_id)
    orders_repo.mark_item_delivered(first["id"])
    assert orders_repo.attention_count() == 1  # one item still outstanding
    orders_repo.mark_item_delivered(second["id"])
    assert orders_repo.attention_count() == 0  # all delivered (status not yet moved)
    assert orders_repo.list_for_admin("attention", 10, 0) == []


def test_listing_is_newest_first_and_paged(db):
    ids = build_orders(db)
    ordered = [r["id"] for r in orders_repo.list_for_admin("all", 10, 0)]
    assert ordered == sorted(ids.values(), reverse=True)
    assert [r["id"] for r in orders_repo.list_for_admin("all", 2, 2)] == ordered[2:4]
    assert orders_repo.list_for_admin("all", 10, 100) == []


def test_counts_by_filter_has_every_filter(db):
    build_orders(db)
    counts = orders_repo.counts_by_filter()
    assert set(counts) == set(orders_repo.ADMIN_FILTERS)
    assert counts == {
        "all": 6,
        "pending_payment": 1,
        "paid": 1,
        "delivered": 1,
        "refunded": 1,
        "closed": 2,
        "attention": 1,
    }


def test_an_unknown_filter_is_rejected_not_interpolated(db):
    for bad in ("nope", "all; DROP TABLE orders", "1=1 --", ""):
        with pytest.raises(ValueError, match="Unknown order filter"):
            orders_repo.list_for_admin(bad, 10, 0)
        with pytest.raises(ValueError, match="Unknown order filter"):
            orders_repo.count_for_admin(bad)
    assert db.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0


def test_the_admin_card_has_order_buyer_items_and_the_last_five_events(db):
    order_id = place_order(USER, {TEXT: 1, KEY: 2})
    customers_repo.upsert(USER, "alice", "Alice")
    pay(order_id)
    for n in range(6):
        orders_repo.add_note(order_id, "system", f"note {n}")

    card = orders_repo.get_admin_card(order_id)

    assert card is not None
    assert card.order["id"] == order_id and card.order["status"] == "paid"
    assert (card.buyer["first_name"], card.buyer["username"], card.buyer["user_id"]) == (
        "Alice",
        "alice",
        USER,
    )
    assert [(i["delivery_kind"], i["delivered_at"]) for i in card.items] == [
        ("text", None),
        ("key", None),
    ]
    assert [e["note"] for e in card.events] == [
        f"note {n}" for n in range(1, 6)
    ]  # last 5, oldest first
    assert len(card.events) == 5


def test_the_admin_card_for_a_missing_order_and_a_buyer_without_a_row(db):
    assert orders_repo.get_admin_card(999) is None
    order_id = place_order(USER)
    db.execute("PRAGMA foreign_keys = OFF")
    db.execute("DELETE FROM customers WHERE user_id = ?", (USER,))
    db.commit()
    db.execute("PRAGMA foreign_keys = ON")
    card = orders_repo.get_admin_card(order_id)
    assert card is not None and card.buyer is None


# --- mark_refunded ----------------------------------------------------------------------------


@pytest.mark.parametrize("start", ["paid", "delivered"])
def test_mark_refunded_changes_status_and_revokes_sold_keys_together(db, start):
    order_id = place_order(USER, {KEY: 3})
    pay(order_id)
    if start == "delivered":
        orders_repo.set_status(order_id, "delivered", "system")

    assert orders_repo.mark_refunded(order_id, "owner", note="goodwill") == 3

    order = orders_repo.get(order_id)
    assert order["status"] == "refunded" and order["refunded_at"] is not None
    assert key_states(db) == {"available": 7, "revoked": 3}
    event = orders_repo.list_events(order_id)[-1]
    assert (event["from_status"], event["to_status"], event["actor"], event["note"]) == (
        start,
        "refunded",
        "owner",
        "goodwill",
    )


@pytest.mark.parametrize("status", ["pending_payment", "cancelled", "expired", "refunded"])
def test_mark_refunded_is_illegal_from_other_statuses_and_changes_nothing(db, status):
    order_id = place_order(USER, {KEY: 2})
    if status == "cancelled":
        orders_repo.cancel_order(order_id, "customer")
    elif status == "expired":
        orders_repo.set_status(order_id, "expired", "system")
    elif status == "refunded":
        pay(order_id)
        orders_repo.mark_refunded(order_id, "owner")
    keys_before = key_states(db)
    events_before = len(orders_repo.list_events(order_id))

    with pytest.raises(IllegalTransitionError):
        orders_repo.mark_refunded(order_id, "owner")

    assert orders_repo.get(order_id)["status"] == status
    assert key_states(db) == keys_before
    assert len(orders_repo.list_events(order_id)) == events_before


def test_mark_refunded_is_atomic_when_revoking_keys_fails(db, monkeypatch):
    order_id = place_order(USER, {KEY: 3})
    pay(order_id)
    keys_before = key_states(db)
    events_before = len(orders_repo.list_events(order_id))

    def boom(conn, order_id):
        raise RuntimeError("revoke failed")

    monkeypatch.setattr(keys_repo, "revoke_for_order_in", boom)
    with pytest.raises(RuntimeError):
        orders_repo.mark_refunded(order_id, "owner")

    order = orders_repo.get(order_id)
    assert order["status"] == "paid" and order["refunded_at"] is None
    assert key_states(db) == keys_before
    assert len(orders_repo.list_events(order_id)) == events_before


def test_mark_refunded_is_atomic_when_the_event_insert_fails(db):
    order_id = place_order(USER, {KEY: 3})
    pay(order_id)
    db.execute(
        "CREATE TEMP TRIGGER fail_event BEFORE INSERT ON order_events"
        " BEGIN SELECT RAISE(ABORT, 'forced'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        orders_repo.mark_refunded(order_id, "owner")
    db.execute("DROP TRIGGER fail_event")
    assert orders_repo.get(order_id)["status"] == "paid"
    assert key_states(db) == {"available": 7, "sold": 3}  # keys were not revoked either


def test_mark_refunded_for_a_missing_order(db):
    with pytest.raises(orders_repo.OrderError):
        orders_repo.mark_refunded(99999, "owner")
