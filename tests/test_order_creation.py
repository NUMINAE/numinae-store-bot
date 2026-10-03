"""create_order, extend_reservation, and set_status/add_note extras."""

from __future__ import annotations

import sqlite3

import pytest

from bot.repo import cart as cart_repo
from bot.repo import orders as orders_repo
from bot.repo import products as products_repo
from bot.repo import settings as settings_repo
from bot.repo import variants as variants_repo
from bot.repo.orders import (
    ALLOWED_TRANSITIONS,
    EmptyCartError,
    InsufficientStock,
    OpenOrderExists,
)

from .helpers import add_customer
from .shop import KEY, TEXT, expire_now, fill_cart, key_states, place_order

USER = 3001


def _cart_snapshot(user_id: int) -> list[tuple[int, int]]:
    return [(ln["variant_id"], ln["quantity"]) for ln in cart_repo.list_lines(user_id)]


def _counts(conn) -> dict[str, int]:
    return {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: S608 - literals
        for t in ("orders", "order_items", "order_events", "cart_items")
    }


# --- create_order -----------------------------------------------------------------------------


def test_create_order_snapshots_lines_and_totals(db):
    fill_cart(USER, {TEXT: 1, KEY: 3})
    created = orders_repo.create_order(USER, "stars")
    assert created.dropped_count == 0

    order, items = orders_repo.get_with_items(created.order_id)
    assert (order["status"], order["payment_mode"], order["user_id"]) == (
        "pending_payment",
        "stars",
        USER,
    )
    assert order["total_stars"] == 75 + 3 * 50
    assert order["item_count"] == 4
    by_kind = {i["delivery_kind"]: i for i in items}
    assert by_kind["text"]["product_name"] == "Neon Portrait Prompt Pack"
    assert by_kind["text"]["variant_label"] == "Personal"
    assert (by_kind["key"]["unit_price_stars"], by_kind["key"]["quantity"]) == (50, 3)
    assert by_kind["key"]["line_total_stars"] == 150
    assert all(i["delivered_at"] is None for i in items)


def test_snapshot_survives_later_catalogue_edits(db):
    order_id = place_order(USER, {TEXT: 1})
    variants_repo.update_price(TEXT, 999)
    products_repo.update_name(1, "Renamed")
    (item,) = orders_repo.get_with_items(order_id)[1]
    assert (item["unit_price_stars"], item["product_name"]) == (75, "Neon Portrait Prompt Pack")


def test_create_order_reserves_exactly_n_keys_and_ties_them_to_the_item(db):
    order_id = place_order(USER, {KEY: 4})
    assert key_states(db) == {"available": 6, "reserved": 4}
    (item,) = orders_repo.get_with_items(order_id)[1]
    order = orders_repo.get(order_id)
    rows = db.execute("SELECT * FROM license_keys WHERE status = 'reserved'").fetchall()
    assert {r["order_item_id"] for r in rows} == {item["id"]}
    assert {r["reserved_until"] for r in rows} == {order["expires_at"]}


def test_expiry_uses_the_reservation_minutes_setting(db):
    settings_repo.set("reservation_minutes", "30")
    order_id = place_order(USER)
    order = orders_repo.get(order_id)
    minutes = db.execute(
        "SELECT CAST((julianday(?) - julianday('now')) * 1440 + 0.5 AS INTEGER) AS m",
        (order["expires_at"],),
    ).fetchone()["m"]
    assert minutes == 30


def test_bad_reservation_setting_falls_back_to_the_default(db):
    settings_repo.set("reservation_minutes", "soon")
    order_id = place_order(USER)
    assert orders_repo.get(order_id)["expires_at"] is not None


def test_create_order_clears_the_cart_and_writes_the_creation_event(db):
    order_id = place_order(USER, {TEXT: 1, KEY: 2})
    assert cart_repo.list_lines(USER) == []
    (event,) = orders_repo.list_events(order_id)
    assert (event["from_status"], event["to_status"], event["actor"]) == (
        None,
        "pending_payment",
        "customer",
    )


def test_a_second_open_order_raises_open_order_exists_without_touching_anything(db):
    first = place_order(USER, {TEXT: 1})
    fill_cart(USER, {KEY: 2})
    before = _counts(db), key_states(db), _cart_snapshot(USER)

    with pytest.raises(OpenOrderExists) as info:
        orders_repo.create_order(USER, "stars")

    assert info.value.order_id == first
    assert "sqlite" not in str(info.value).lower() and "UNIQUE" not in str(info.value)
    assert (_counts(db), key_states(db), _cart_snapshot(USER)) == before


def test_the_unique_index_race_is_mapped_to_open_order_exists(db, monkeypatch):
    """If another writer slips an open order in after the pre-check, the unique
    index fires; the IntegrityError must surface as OpenOrderExists."""
    first = place_order(USER, {TEXT: 1})
    fill_cart(USER, {KEY: 1})
    real = orders_repo.get_open_for_user
    calls = {"n": 0}

    def blind_first_time(user_id):
        calls["n"] += 1
        return None if calls["n"] == 1 else real(user_id)

    monkeypatch.setattr(orders_repo, "get_open_for_user", blind_first_time)
    with pytest.raises(OpenOrderExists) as info:
        orders_repo.create_order(USER, "stars")
    assert info.value.order_id == first
    assert key_states(db) == {"available": 10}  # rolled back


def test_empty_cart_raises(db):
    add_customer(USER)
    with pytest.raises(EmptyCartError):
        orders_repo.create_order(USER, "stars")


def test_unpurchasable_lines_are_dropped_and_counted(db):
    fill_cart(USER, {TEXT: 1, 2: 1})
    variants_repo.set_active(2, False)
    created = orders_repo.create_order(USER, "stars")
    assert created.dropped_count == 1
    (item,) = orders_repo.get_with_items(created.order_id)[1]
    assert item["variant_id"] == TEXT
    assert cart_repo.list_lines(USER) == []  # the dropped line is cleared too


def test_a_cart_of_only_unpurchasable_lines_is_empty_and_changes_nothing(db):
    fill_cart(USER, {TEXT: 1})
    variants_repo.set_active(TEXT, False)
    before = _counts(db)
    with pytest.raises(EmptyCartError):
        orders_repo.create_order(USER, "stars")
    assert _counts(db) == before and _cart_snapshot(USER) == [(TEXT, 1)]


def test_insufficient_stock_rolls_everything_back(db):
    fill_cart(USER, {TEXT: 1, KEY: 5})
    # Other buyers take 8 of the 10 keys first, leaving 2 (so the line is still
    # purchasable but cannot cover 5).
    place_order(3002, {KEY: 8})
    before = _counts(db), key_states(db), _cart_snapshot(USER)

    with pytest.raises(InsufficientStock) as info:
        orders_repo.create_order(USER, "stars")

    assert (info.value.variant_id, info.value.available) == (KEY, 2)
    assert (_counts(db), key_states(db), _cart_snapshot(USER)) == before


def test_twenty_sequential_buyers_on_ten_keys_give_exactly_ten_orders(db):
    buyers = list(range(4000, 4020))
    for buyer in buyers:
        fill_cart(buyer, {KEY: 1})  # every cart is built while stock still exists
    outcomes = []
    for buyer in buyers:
        try:
            outcomes.append(orders_repo.create_order(buyer, "stars").order_id)
        except (InsufficientStock, EmptyCartError) as exc:
            outcomes.append(exc)

    orders = [o for o in outcomes if isinstance(o, int)]
    failures = [o for o in outcomes if not isinstance(o, int)]
    assert len(orders) == 10 and len(failures) == 10
    keys = [r["key_value"] for r in db.execute("SELECT key_value FROM license_keys")]
    assert len(set(keys)) == len(keys)
    reserved = db.execute(
        "SELECT order_item_id FROM license_keys WHERE status = 'reserved'"
    ).fetchall()
    assert len(reserved) == 10 and len({r["order_item_id"] for r in reserved}) == 10
    assert key_states(db) == {"reserved": 10}


def test_a_lapsed_reservation_can_be_reclaimed_by_another_buyer(db):
    fill_cart(3002, {KEY: 5})  # built while all 10 keys were in stock
    first = place_order(USER, {KEY: 8})  # 2 keys left available
    expire_now(db, first)  # ... and the 8 reservations have lapsed

    second = orders_repo.create_order(3002, "stars").order_id

    # Keys 1-5 were the lapsed ones; 6-8 are still the first order's, 9-10 untouched.
    assert key_states(db) == {"available": 2, "reserved": 8}
    second_item = orders_repo.get_with_items(second)[1][0]["id"]
    mine = db.execute(
        "SELECT COUNT(*) FROM license_keys WHERE order_item_id = ?", (second_item,)
    ).fetchone()[0]
    assert mine == 5

    # Expiring the first order later must release only the keys it still holds.
    assert orders_repo.expire_due_orders() == [(first, USER)]
    assert key_states(db) == {"available": 5, "reserved": 5}
    held = db.execute(
        "SELECT COUNT(*) FROM license_keys WHERE status = 'reserved' AND order_item_id = ?",
        (second_item,),
    ).fetchone()[0]
    assert held == 5


# --- extend_reservation -----------------------------------------------------------------------


def test_extend_reservation_pushes_order_and_keys_forward(db):
    order_id = place_order(USER, {KEY: 2})
    db.execute(
        "UPDATE orders SET expires_at = datetime('now', '+1 minutes') WHERE id = ?", (order_id,)
    )
    db.execute("UPDATE license_keys SET reserved_until = datetime('now', '+1 minutes')")
    db.commit()

    assert orders_repo.extend_reservation(order_id, 5) is True

    order = orders_repo.get(order_id)
    soon = db.execute("SELECT datetime('now', '+4 minutes') AS t").fetchone()["t"]
    assert order["expires_at"] >= soon
    reserved = db.execute(
        "SELECT reserved_until FROM license_keys WHERE status = 'reserved'"
    ).fetchall()
    assert len(reserved) == 2 and all(r["reserved_until"] >= soon for r in reserved)


def test_extend_reservation_never_shortens(db):
    order_id = place_order(USER, {KEY: 1})
    before = orders_repo.get(order_id)["expires_at"]
    orders_repo.extend_reservation(order_id, 1)
    assert orders_repo.get(order_id)["expires_at"] == before


def test_extend_reservation_only_touches_pending_orders(db):
    order_id = place_order(USER)
    orders_repo.set_status(order_id, "cancelled", "customer")
    assert orders_repo.extend_reservation(order_id, 5) is False
    assert orders_repo.extend_reservation(99999, 5) is False


def test_reserved_key_quantity_needed_counts_key_lines_only(db):
    order_id = place_order(USER, {TEXT: 1, KEY: 3})
    assert orders_repo.reserved_key_quantity_needed(order_id) == 3


def test_is_expired(db):
    order_id = place_order(USER)
    assert orders_repo.is_expired(orders_repo.get(order_id)) is False
    expire_now(db, order_id)
    assert orders_repo.is_expired(orders_repo.get(order_id)) is True


# --- set_status extras / add_note -------------------------------------------------------------


def test_set_status_writes_the_whitelisted_extra_column_atomically(db):
    order_id = place_order(USER)
    orders_repo.set_status(order_id, "paid", "system", extra={"telegram_payment_charge_id": "ch_x"})
    order = orders_repo.get(order_id)
    assert (order["status"], order["telegram_payment_charge_id"]) == ("paid", "ch_x")
    assert order["paid_at"] is not None


@pytest.mark.parametrize("column", ["total_stars", "user_id", "status", "paid_at", "id", "x"])
def test_set_status_rejects_non_whitelisted_columns(db, column):
    order_id = place_order(USER)
    with pytest.raises(ValueError, match="not allowed"):
        orders_repo.set_status(order_id, "paid", "system", extra={column: 1})
    assert orders_repo.get(order_id)["status"] == "pending_payment"
    assert len(orders_repo.list_events(order_id)) == 1


def test_set_status_extra_is_rolled_back_with_an_illegal_transition(db):
    order_id = place_order(USER)
    with pytest.raises(orders_repo.IllegalTransitionError):
        orders_repo.set_status(
            order_id, "delivered", "system", extra={"telegram_payment_charge_id": "ch_y"}
        )
    assert orders_repo.get(order_id)["telegram_payment_charge_id"] is None


def test_a_duplicate_charge_id_rolls_back_the_whole_status_change(db):
    first = place_order(USER)
    second = place_order(3002)
    orders_repo.set_status(first, "paid", "system", extra={"telegram_payment_charge_id": "dup"})
    with pytest.raises(sqlite3.IntegrityError):
        orders_repo.set_status(
            second, "paid", "system", extra={"telegram_payment_charge_id": "dup"}
        )
    assert orders_repo.get(second)["status"] == "pending_payment"
    assert len(orders_repo.list_events(second)) == 1


def test_add_note_writes_an_event_without_changing_status(db):
    order_id = place_order(USER)
    orders_repo.add_note(order_id, "system", "something happened")
    order = orders_repo.get(order_id)
    assert order["status"] == "pending_payment"
    note = orders_repo.list_events(order_id)[-1]
    assert (note["from_status"], note["to_status"], note["note"]) == (
        "pending_payment",
        "pending_payment",
        "something happened",
    )
    with pytest.raises(orders_repo.OrderError):
        orders_repo.add_note(99999, "system", "x")


def test_the_transition_table_is_unchanged():
    assert ALLOWED_TRANSITIONS["pending_payment"] == {"paid", "cancelled", "expired"}


def test_create_order_is_atomic_when_a_late_write_fails(db):
    """A failure after the order, its items and the key reservations were written
    (the creation event insert) must undo all of it: no order rows, no reserved
    keys, and the cart exactly as it was."""
    fill_cart(USER, {TEXT: 1, KEY: 3})
    cart_before = _cart_snapshot(USER)
    counts_before = _counts(db)
    db.execute(
        "CREATE TEMP TRIGGER fail_event BEFORE INSERT ON order_events"
        " BEGIN SELECT RAISE(ABORT, 'forced failure'); END"
    )

    with pytest.raises(sqlite3.IntegrityError, match="forced failure"):
        orders_repo.create_order(USER, "stars")

    db.execute("DROP TRIGGER fail_event")
    assert _counts(db) == counts_before
    assert key_states(db) == {"available": 10}
    assert _cart_snapshot(USER) == cart_before
    assert orders_repo.get_open_for_user(USER) is None
    # ... and the very same cart can be ordered once the fault is gone.
    assert orders_repo.create_order(USER, "stars").dropped_count == 0
