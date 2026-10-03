from __future__ import annotations

import sqlite3

import pytest

from bot.repo import cart as cart_repo
from bot.repo import orders as orders_repo
from bot.repo import products as products_repo
from bot.repo import variants as variants_repo
from bot.repo.orders import (
    ALLOWED_TRANSITIONS,
    ActiveOrderExistsError,
    CartEmptyError,
    IllegalTransitionError,
    OrderError,
    PaymentMethodUnavailableError,
)

from .helpers import add_customer

USER = 1001
OTHER = 1002
V1 = 1  # product 1 "Persian Saffron Black Tea", 100g, 600 cents
V2 = 2  # same product, 250g, 1400 cents
PM = "BTC"
WALLET = "test-wallet-address"

# Deliberately a literal copy, NOT derived from ALLOWED_TRANSITIONS: a test
# that read its expectations from the table could never notice the table
# changing.
EXPECTED_TRANSITIONS: dict[str, set[str]] = {
    "awaiting_payment": {"awaiting_verification", "cancelled", "expired"},
    "awaiting_verification": {"confirmed", "rejected", "cancelled"},
    "rejected": {"awaiting_verification", "cancelled", "expired"},
    "awaiting_shipping": {"confirmed"},
    "confirmed": {"shipped"},
    "shipped": {"delivered"},
    "delivered": set(),
    "cancelled": set(),
    "expired": set(),
}
ALL_STATUSES = sorted(EXPECTED_TRANSITIONS)


@pytest.fixture
def shop(db):
    """Two customers and a live payment method."""
    add_customer(USER)
    add_customer(OTHER)
    db.execute(
        "UPDATE payment_methods SET is_active = 1, wallet_address = ?, memo_tag = 'memo-1' "
        "WHERE code = ?",
        (WALLET, PM),
    )
    db.commit()
    return db


def _insert_order(conn, status: str, user_id: int = USER) -> int:
    cur = conn.execute(
        """
        INSERT INTO orders (user_id, total_usd, item_count, payment_method, wallet_address, status)
        VALUES (?, 600, 1, ?, ?, ?)
        """,
        (user_id, PM, WALLET, status),
    )
    conn.commit()
    return cur.lastrowid


def _events(conn, order_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM order_events WHERE order_id = ? ORDER BY id", (order_id,)
    ).fetchall()


def _status(conn, order_id: int) -> str:
    return conn.execute("SELECT status FROM orders WHERE id = ?", (order_id,)).fetchone()["status"]


# --- the transition table ---------------------------------------------------


def test_status_set_matches_the_table_and_the_schema(shop):
    assert set(ALLOWED_TRANSITIONS) == set(EXPECTED_TRANSITIONS)
    for targets in ALLOWED_TRANSITIONS.values():
        assert targets <= set(ALLOWED_TRANSITIONS)
    for status in ALL_STATUSES:  # the schema CHECK accepts exactly this set
        _insert_order(shop, status)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_order(shop, "not_a_status")


@pytest.mark.parametrize(
    ("from_status", "to_status"),
    [(f, t) for f in ALL_STATUSES for t in ALL_STATUSES],
)
def test_every_status_pair(shop, from_status, to_status):
    order_id = _insert_order(shop, from_status)
    if to_status in EXPECTED_TRANSITIONS[from_status]:
        orders_repo.set_status(order_id, to_status, "owner", note="n")
        assert _status(shop, order_id) == to_status
        (event,) = _events(shop, order_id)
        assert (event["from_status"], event["to_status"]) == (from_status, to_status)
        assert (event["actor"], event["note"]) == ("owner", "n")
    else:
        with pytest.raises(IllegalTransitionError) as excinfo:
            orders_repo.set_status(order_id, to_status, "owner")
        assert (excinfo.value.from_status, excinfo.value.to_status) == (from_status, to_status)
        assert _status(shop, order_id) == from_status
        assert _events(shop, order_id) == []


def test_set_status_writes_exactly_one_event_per_call(shop):
    order_id = _insert_order(shop, "awaiting_payment")
    orders_repo.set_status(order_id, "awaiting_verification", "customer")
    assert len(_events(shop, order_id)) == 1
    orders_repo.set_status(order_id, "confirmed", "owner")
    assert [e["to_status"] for e in _events(shop, order_id)] == [
        "awaiting_verification",
        "confirmed",
    ]


def test_unknown_target_status_is_illegal(shop):
    order_id = _insert_order(shop, "awaiting_payment")
    with pytest.raises(IllegalTransitionError):
        orders_repo.set_status(order_id, "teleported", "owner")
    assert _events(shop, order_id) == []


def test_set_status_on_missing_order_raises(shop):
    with pytest.raises(OrderError):
        orders_repo.set_status(9999, "cancelled", "owner")


# --- other status-changing paths -------------------------------------------


def test_submit_tx_hash_moves_to_verification(shop):
    order_id = _insert_order(shop, "awaiting_payment")
    orders_repo.submit_tx_hash(order_id, "hash-1")
    row = orders_repo.get(order_id)
    assert (row["status"], row["tx_hash"], row["attempt_count"]) == (
        "awaiting_verification",
        "hash-1",
        1,
    )
    assert len(_events(shop, order_id)) == 1


def test_submit_tx_hash_from_wrong_status_is_illegal(shop):
    order_id = _insert_order(shop, "shipped")
    with pytest.raises(IllegalTransitionError):
        orders_repo.submit_tx_hash(order_id, "hash-1")
    assert _events(shop, order_id) == []


def test_live_tx_hash_is_unique_per_method(shop):
    first = _insert_order(shop, "awaiting_payment")
    second = _insert_order(shop, "awaiting_payment", user_id=OTHER)
    orders_repo.submit_tx_hash(first, "dup")
    with pytest.raises(sqlite3.IntegrityError):
        orders_repo.submit_tx_hash(second, "dup")


def test_reject_stores_reason_and_event(shop):
    order_id = _insert_order(shop, "awaiting_verification")
    orders_repo.reject(order_id, "owner", "wrong amount")
    row = orders_repo.get(order_id)
    assert (row["status"], row["reject_reason"]) == ("rejected", "wrong amount")
    assert _events(shop, order_id)[0]["note"] == "Rejected: wrong amount"


def test_reject_from_wrong_status_is_illegal(shop):
    order_id = _insert_order(shop, "confirmed")
    with pytest.raises(IllegalTransitionError):
        orders_repo.reject(order_id, "owner", "x")


def test_cancel_restores_the_cart_from_snapshots(shop):
    cart_repo.add(USER, V1, 2)
    order_id, _ = orders_repo.create_from_cart(USER, PM, "somewhere")
    assert cart_repo.list_lines(USER) == []
    orders_repo.cancel(order_id, "customer")
    assert orders_repo.get(order_id)["status"] == "cancelled"
    assert [(r["variant_id"], r["quantity"]) for r in cart_repo.list_lines(USER)] == [(V1, 2)]
    assert _events(shop, order_id)[-1]["note"] == "Cancelled by customer"


def test_cancel_from_a_non_cancellable_status_is_illegal(shop):
    order_id = _insert_order(shop, "confirmed")
    with pytest.raises(IllegalTransitionError):
        orders_repo.cancel(order_id, "owner")
    assert _status(shop, order_id) == "confirmed"


def test_cancel_missing_order_raises(shop):
    with pytest.raises(OrderError):
        orders_repo.cancel(9999, "owner")


# --- create_from_cart -------------------------------------------------------


def test_create_from_cart_snapshots_names_labels_and_prices(shop):
    cart_repo.add(USER, V1, 2)
    cart_repo.add(USER, V2, 1)
    order_id, dropped = orders_repo.create_from_cart(USER, PM, "Old Town square")
    assert dropped == 0

    order, items = orders_repo.get_with_items(order_id)
    assert (order["total_usd"], order["item_count"]) == (2 * 600 + 1400, 3)
    assert order["status"] == "awaiting_payment"
    assert (order["wallet_address"], order["memo_tag"]) == (WALLET, "memo-1")
    assert order["shipping_text"] == "Old Town square"
    assert order["expires_at"] is not None
    snapshot = [
        (i["variant_id"], i["product_name"], i["variant_label"], i["unit_price_usd"],
         i["quantity"], i["line_total_usd"])
        for i in items
    ]  # fmt: skip
    assert sorted(snapshot) == [
        (V1, "Persian Saffron Black Tea", "100g", 600, 2, 1200),
        (V2, "Persian Saffron Black Tea", "250g", 1400, 1, 1400),
    ]

    # The order keeps its own copy: later catalogue edits must not rewrite it.
    products_repo.update_name(1, "Renamed")
    variants_repo.update_label(V1, "9kg")
    variants_repo.update_price(V1, 99999)
    wallet_before = order["wallet_address"]
    shop.execute("UPDATE payment_methods SET wallet_address = 'changed' WHERE code = ?", (PM,))
    shop.commit()
    after_order, after_items = orders_repo.get_with_items(order_id)
    assert after_order["wallet_address"] == wallet_before
    by_variant = {i["variant_id"]: i for i in after_items}
    assert by_variant[V1]["product_name"] == "Persian Saffron Black Tea"
    assert by_variant[V1]["variant_label"] == "100g"
    assert by_variant[V1]["unit_price_usd"] == 600


def test_create_from_cart_clears_cart_logs_event_and_remembers_address(shop):
    cart_repo.add(USER, V1, 1)
    order_id, _ = orders_repo.create_from_cart(USER, PM, "my address")
    assert cart_repo.list_lines(USER) == []
    (event,) = _events(shop, order_id)
    assert (event["from_status"], event["to_status"], event["actor"]) == (
        None,
        "awaiting_payment",
        "system",
    )
    row = shop.execute("SELECT last_shipping_text FROM customers WHERE user_id = ?", (USER,))
    assert row.fetchone()["last_shipping_text"] == "my address"
    assert orders_repo.get_active_for_user(USER)["id"] == order_id


def test_create_from_cart_drops_unavailable_lines(shop):
    cart_repo.add(USER, V1, 1)
    cart_repo.add(USER, V2, 1)
    variants_repo.set_active(V2, False)
    order_id, dropped = orders_repo.create_from_cart(USER, PM, "x")
    assert dropped == 1
    _, items = orders_repo.get_with_items(order_id)
    assert [i["variant_id"] for i in items] == [V1]
    assert cart_repo.list_lines(USER) == []


def test_create_from_cart_empty_cart(shop):
    with pytest.raises(CartEmptyError):
        orders_repo.create_from_cart(USER, PM, "x")


def test_create_from_cart_with_only_unavailable_lines(shop):
    cart_repo.add(USER, V1, 1)
    variants_repo.set_active(V1, False)
    with pytest.raises(CartEmptyError):
        orders_repo.create_from_cart(USER, PM, "x")
    assert len(cart_repo.list_lines(USER)) == 1


def test_create_from_cart_rejects_a_second_active_order(shop):
    cart_repo.add(USER, V1, 1)
    first, _ = orders_repo.create_from_cart(USER, PM, "x")
    cart_repo.add(USER, V2, 1)
    with pytest.raises(ActiveOrderExistsError) as excinfo:
        orders_repo.create_from_cart(USER, PM, "x")
    assert excinfo.value.order_id == first
    assert len(cart_repo.list_lines(USER)) == 1  # untouched


@pytest.mark.parametrize("setup", ["inactive", "no_wallet", "unknown"])
def test_create_from_cart_needs_a_usable_payment_method(shop, setup):
    code = PM
    if setup == "inactive":
        shop.execute("UPDATE payment_methods SET is_active = 0 WHERE code = ?", (PM,))
    elif setup == "no_wallet":
        shop.execute("UPDATE payment_methods SET wallet_address = '' WHERE code = ?", (PM,))
    else:
        code = "NOPE"
    shop.commit()
    cart_repo.add(USER, V1, 1)
    with pytest.raises(PaymentMethodUnavailableError):
        orders_repo.create_from_cart(USER, code, "x")
    assert len(cart_repo.list_lines(USER)) == 1


@pytest.mark.parametrize("failing_table", ["order_items", "order_events", "cart_items"])
def test_create_from_cart_rolls_back_completely_when_a_step_fails(shop, failing_table):
    cart_repo.add(USER, V1, 2)
    cart_repo.add(USER, V2, 1)
    before_text = shop.execute(
        "SELECT last_shipping_text FROM customers WHERE user_id = ?", (USER,)
    ).fetchone()[0]
    event = "DELETE" if failing_table == "cart_items" else "INSERT"
    shop.execute(
        f"CREATE TRIGGER fail_step BEFORE {event} ON {failing_table} "  # noqa: S608 - test literals
        "BEGIN SELECT RAISE(ABORT, 'induced failure'); END"
    )
    shop.commit()

    with pytest.raises(sqlite3.DatabaseError, match="induced failure"):
        orders_repo.create_from_cart(USER, PM, "new address")

    count = lambda table: shop.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: E731, S608
    assert count("orders") == 0
    assert count("order_items") == 0
    assert count("order_events") == 0
    assert sorted((r["variant_id"], r["quantity"]) for r in cart_repo.list_lines(USER)) == [
        (V1, 2),
        (V2, 1),
    ]
    after_text = shop.execute(
        "SELECT last_shipping_text FROM customers WHERE user_id = ?", (USER,)
    ).fetchone()[0]
    assert after_text == before_text


# --- reads and counters -----------------------------------------------------


def test_listing_and_counting(shop):
    _insert_order(shop, "awaiting_payment")
    verify = _insert_order(shop, "awaiting_verification", user_id=OTHER)
    _insert_order(shop, "confirmed", user_id=OTHER)
    _insert_order(shop, "delivered", user_id=OTHER)

    assert orders_repo.count_for_user(USER) == 1
    assert orders_repo.count_for_user(OTHER) == 3
    assert len(orders_repo.list_for_user(OTHER, 2, 0)) == 2
    assert len(orders_repo.list_for_user(OTHER, 2, 2)) == 1
    assert orders_repo.count_by_status("confirmed") == 1
    assert orders_repo.count_needing_attention() == 2
    assert orders_repo.count_by_filter("all") == 4
    assert orders_repo.count_by_filter("verify") == 1
    assert orders_repo.count_by_filter("closed") == 1
    assert orders_repo.count_by_filter("bogus-filter") == 0
    assert [o["id"] for o in orders_repo.list_by_filter("verify", 10, 0)] == [verify]
    assert len(orders_repo.list_by_filter("all", 10, 0)) == 4
    assert orders_repo.get(9999) is None
    assert orders_repo.get_with_items(9999) == (None, [])
    assert orders_repo.get_active_for_user(OTHER)["id"] == verify
    assert orders_repo.count_open_for_method(PM) == 1


def test_active_order_ignores_terminal_statuses(shop):
    _insert_order(shop, "delivered")
    _insert_order(shop, "cancelled")
    assert orders_repo.get_active_for_user(USER) is None
    live = _insert_order(shop, "rejected")
    assert orders_repo.get_active_for_user(USER)["id"] == live


def test_set_tracking_note_and_reminders(shop):
    order_id = _insert_order(shop, "shipped")
    orders_repo.set_tracking_note(order_id, "track-1")
    orders_repo.set_reminders_sent(order_id, 2)
    row = orders_repo.get(order_id)
    assert (row["tracking_note"], row["reminders_sent"]) == ("track-1", 2)


def test_count_expired_since_reads_events(shop):
    order_id = _insert_order(shop, "awaiting_payment")
    assert orders_repo.count_expired_since(7) == 0
    orders_repo.set_status(order_id, "expired", "system")
    assert orders_repo.count_expired_since(7) == 1
