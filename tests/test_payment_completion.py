"""complete_payment outcomes, cancel_order and expire_due_orders."""

from __future__ import annotations

import logging

import pytest

from bot.repo import cart as cart_repo
from bot.repo import license_keys as keys_repo
from bot.repo import orders as orders_repo
from bot.repo.orders import IllegalTransitionError, OrderError, PaymentOutcome

from .shop import KEY, TEXT, expire_now, fill_cart, key_states, pay, place_order

USER = 3001
OTHER = 3002


def _status(order_id: int) -> str:
    order = orders_repo.get(order_id)
    assert order is not None
    return str(order["status"])


def _unchanged(db, order_id: int):
    order = orders_repo.get(order_id)
    assert order is not None
    return (
        order["status"],
        order["telegram_payment_charge_id"],
        len(orders_repo.list_events(order_id)),
        key_states(db),
    )


# --- complete_payment -------------------------------------------------------------------------


def test_paid_moves_status_stores_the_charge_id_and_sells_the_keys(db):
    order_id = place_order(USER, {TEXT: 1, KEY: 3})
    assert key_states(db) == {"available": 7, "reserved": 3}

    assert pay(order_id, "ch_ok") is PaymentOutcome.PAID

    order = orders_repo.get(order_id)
    assert (order["status"], order["telegram_payment_charge_id"]) == ("paid", "ch_ok")
    assert order["paid_at"] is not None
    assert key_states(db) == {"available": 7, "sold": 3}
    assert (
        db.execute("SELECT COUNT(*) FROM license_keys WHERE reserved_until IS NOT NULL").fetchone()[
            0
        ]
        == 0
    )
    last = orders_repo.list_events(order_id)[-1]
    assert (last["from_status"], last["to_status"]) == ("pending_payment", "paid")


def test_a_second_identical_call_is_a_duplicate_and_changes_nothing(db):
    order_id = place_order(USER, {KEY: 2})
    assert pay(order_id, "ch_dup") is PaymentOutcome.PAID
    snapshot = _unchanged(db, order_id)
    assert pay(order_id, "ch_dup") is PaymentOutcome.DUPLICATE
    assert _unchanged(db, order_id) == snapshot


def test_a_charge_id_already_used_by_another_order_is_a_duplicate(db):
    first = place_order(USER)
    second = place_order(OTHER)
    assert pay(first, "ch_shared") is PaymentOutcome.PAID
    assert pay(second, "ch_shared") is PaymentOutcome.DUPLICATE
    assert _status(second) == "pending_payment"
    assert len(orders_repo.list_events(second)) == 1


def test_an_unrelated_integrity_error_is_not_swallowed_as_a_duplicate(db, monkeypatch):
    import sqlite3

    order_id = place_order(USER)

    def boom(*args, **kwargs):
        raise sqlite3.IntegrityError("FOREIGN KEY constraint failed")

    monkeypatch.setattr(orders_repo, "set_status_in_txn", boom)
    with pytest.raises(sqlite3.IntegrityError):
        pay(order_id)
    assert _status(order_id) == "pending_payment"


@pytest.mark.parametrize("final", ["expired", "cancelled"])
def test_payment_on_a_dead_order_is_an_orphan_with_an_audit_note(db, final):
    order_id = place_order(USER, {KEY: 2})
    if final == "expired":
        expire_now(db, order_id)
        orders_repo.expire_due_orders()
    else:
        orders_repo.cancel_order(order_id, "customer")
    keys_before = key_states(db)

    assert pay(order_id, "ch_late") is PaymentOutcome.ORPHAN

    order = orders_repo.get(order_id)
    assert order["status"] == final
    assert order["telegram_payment_charge_id"] is None  # not recorded as a sale
    note = orders_repo.list_events(order_id)[-1]
    assert note["from_status"] == note["to_status"] == final
    assert "ch_late" in note["note"]
    assert key_states(db) == keys_before  # nothing was sold


def test_a_different_charge_on_an_already_paid_order_is_an_orphan(db):
    order_id = place_order(USER)
    assert pay(order_id, "ch_a") is PaymentOutcome.PAID
    assert pay(order_id, "ch_b") is PaymentOutcome.ORPHAN
    order = orders_repo.get(order_id)
    assert (order["status"], order["telegram_payment_charge_id"]) == ("paid", "ch_a")
    assert "ch_b" in orders_repo.list_events(order_id)[-1]["note"]


def _mismatch_cases(order_id: int, order):
    total = order["total_stars"]
    return {
        "wrong_user": (order_id, OTHER, "c", total, "XTR"),
        "wrong_amount_low": (order_id, order["user_id"], "c", total - 1, "XTR"),
        "wrong_amount_high": (order_id, order["user_id"], "c", total + 1, "XTR"),
        "wrong_currency": (order_id, order["user_id"], "c", total, "USD"),
        "missing_order": (999999, order["user_id"], "c", total, "XTR"),
    }


@pytest.mark.parametrize(
    "cause",
    ["wrong_user", "wrong_amount_low", "wrong_amount_high", "wrong_currency", "missing_order"],
)
def test_mismatches_change_nothing(db, cause):
    order_id = place_order(USER, {KEY: 2})
    snapshot = _unchanged(db, order_id)
    args = _mismatch_cases(order_id, orders_repo.get(order_id))[cause]

    assert orders_repo.complete_payment(*args).outcome is PaymentOutcome.MISMATCH

    assert _unchanged(db, order_id) == snapshot
    assert key_states(db) == {"available": 8, "reserved": 2}  # never sold on a mismatch


def test_keys_end_sold_only_on_paid(db):
    order_id = place_order(USER, {KEY: 2})
    pay(order_id, currency="USD")  # mismatch
    assert "sold" not in key_states(db)
    pay(order_id)
    assert key_states(db)["sold"] == 2


def test_currency_is_only_checked_for_stars_orders(db):
    order_id = place_order(USER, mode="mock")
    assert pay(order_id, currency="USD") is PaymentOutcome.PAID


def test_short_key_reservation_is_flagged_in_the_audit_log_but_still_paid(db):
    order_id = place_order(USER, {KEY: 3})
    db.execute(
        "UPDATE license_keys SET status = 'available', order_item_id = NULL"
        " WHERE id = (SELECT MIN(id) FROM license_keys WHERE status = 'reserved')"
    )
    db.commit()
    assert pay(order_id) is PaymentOutcome.PAID
    assert "incomplete" in orders_repo.list_events(order_id)[-1]["note"]


def test_a_failure_while_selling_keys_rolls_the_payment_back(db, monkeypatch):
    order_id = place_order(USER, {KEY: 2})
    snapshot = _unchanged(db, order_id)

    def boom(conn, order_id):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(keys_repo, "sell_reserved_for_order_in", boom)
    with pytest.raises(RuntimeError):
        pay(order_id)
    assert _unchanged(db, order_id) == snapshot


# --- cancel_order -----------------------------------------------------------------------------


def test_customer_cancel_releases_keys_and_restores_the_cart(db):
    order_id = place_order(USER, {TEXT: 1, KEY: 3})
    restored = orders_repo.cancel_order(order_id, "customer")

    assert restored == 2
    order = orders_repo.get(order_id)
    assert order["status"] == "cancelled"
    assert key_states(db) == {"available": 10}
    assert sorted((ln["variant_id"], ln["quantity"]) for ln in cart_repo.list_lines(USER)) == [
        (TEXT, 1),
        (KEY, 3),
    ]
    event = orders_repo.list_events(order_id)[-1]
    assert (event["from_status"], event["to_status"], event["actor"]) == (
        "pending_payment",
        "cancelled",
        "customer",
    )


@pytest.mark.parametrize("actor", ["owner", "system"])
def test_non_customer_cancel_releases_keys_but_leaves_the_cart_alone(db, actor):
    order_id = place_order(USER, {KEY: 3})
    assert orders_repo.cancel_order(order_id, actor) == 0
    assert key_states(db) == {"available": 10}
    assert cart_repo.list_lines(USER) == []
    assert orders_repo.list_events(order_id)[-1]["actor"] == actor


def test_restored_quantities_are_clamped_by_the_cart_limits(db):
    order_id = place_order(USER, {KEY: 5})
    # Two of this order's keys get revoked while it is pending: only 3 will be
    # released back into stock.
    db.execute(
        "UPDATE license_keys SET status = 'revoked' WHERE id IN"
        " (SELECT id FROM license_keys WHERE status = 'reserved' ORDER BY id LIMIT 2)"
    )
    db.commit()
    orders_repo.cancel_order(order_id, "customer")
    assert key_states(db) == {"available": 8, "revoked": 2}  # revoked keys stay revoked
    (line,) = cart_repo.list_lines(USER)
    assert line["quantity"] == 5  # stock is 8, so 5 is within the limit

    # Now clamp for real: only 3 left in stock.
    db.execute(
        "UPDATE license_keys SET status = 'sold' WHERE status = 'available' AND id NOT IN"
        " (SELECT id FROM license_keys WHERE status = 'available' ORDER BY id LIMIT 3)"
    )
    db.commit()
    cart_repo.clear(USER)
    order2 = place_order(USER, {KEY: 3})
    fill_cart(USER, {KEY: 1})
    orders_repo.cancel_order(order2, "customer")
    (line,) = cart_repo.list_lines(USER)
    assert line["quantity"] == 3  # 1 already in the cart + 3 restored, clamped to stock 3


def test_unpurchasable_lines_are_not_restored(db):
    from bot.repo import variants as variants_repo

    order_id = place_order(USER, {TEXT: 1, KEY: 2})
    variants_repo.set_active(TEXT, False)
    assert orders_repo.cancel_order(order_id, "customer") == 1
    assert [ln["variant_id"] for ln in cart_repo.list_lines(USER)] == [KEY]


def test_cancelling_a_settled_order_is_illegal_and_changes_nothing(db):
    order_id = place_order(USER, {KEY: 2})
    pay(order_id)
    snapshot = _unchanged(db, order_id)
    with pytest.raises(IllegalTransitionError):
        orders_repo.cancel_order(order_id, "customer")
    assert _unchanged(db, order_id) == snapshot
    assert cart_repo.list_lines(USER) == []
    with pytest.raises(OrderError):
        orders_repo.cancel_order(99999, "customer")


def test_a_failure_while_releasing_keys_leaves_status_cart_and_keys_unchanged(db, monkeypatch):
    order_id = place_order(USER, {KEY: 2})
    snapshot = _unchanged(db, order_id)

    def boom(conn, order_id):
        raise RuntimeError("release failed")

    monkeypatch.setattr(keys_repo, "release_for_order_in", boom)
    with pytest.raises(RuntimeError):
        orders_repo.cancel_order(order_id, "customer")
    assert _unchanged(db, order_id) == snapshot
    assert cart_repo.list_lines(USER) == []


# --- expire_due_orders ------------------------------------------------------------------------


def test_expiry_releases_keys_writes_events_and_never_restores_the_cart(db):
    due = place_order(USER, {KEY: 3})
    fresh = place_order(OTHER, {KEY: 2})
    expire_now(db, due)

    assert orders_repo.expire_due_orders() == [(due, USER)]

    assert _status(due) == "expired" and _status(fresh) == "pending_payment"
    assert key_states(db) == {"available": 8, "reserved": 2}
    assert cart_repo.list_lines(USER) == []
    event = orders_repo.list_events(due)[-1]
    assert (event["from_status"], event["to_status"], event["actor"]) == (
        "pending_payment",
        "expired",
        "system",
    )


def test_expiry_is_idempotent_and_ignores_settled_orders(db):
    paid = place_order(USER, {KEY: 1})
    pay(paid)
    expire_now(db, paid)
    assert orders_repo.expire_due_orders() == []
    assert _status(paid) == "paid"
    assert orders_repo.expire_due_orders() == []


def test_one_failing_expiry_does_not_block_the_others_and_rolls_itself_back(
    db, monkeypatch, caplog
):
    caplog.set_level(logging.ERROR)
    bad = place_order(USER, {KEY: 2})
    good = place_order(OTHER, {KEY: 1})
    expire_now(db, bad)
    expire_now(db, good)
    real = keys_repo.release_for_order_in

    def flaky(conn, order_id):
        if order_id == bad:
            raise RuntimeError("boom")
        return real(conn, order_id)

    monkeypatch.setattr(keys_repo, "release_for_order_in", flaky)
    assert orders_repo.expire_due_orders() == [(good, OTHER)]

    assert _status(bad) == "pending_payment"  # status rolled back with the failed release
    assert key_states(db) == {"available": 8, "reserved": 2}
    assert any(str(bad) in r.getMessage() for r in caplog.records if r.levelno == logging.ERROR)


def test_release_only_touches_reserved_keys(db):
    order_id = place_order(USER, {KEY: 3})
    db.execute(
        "UPDATE license_keys SET status = 'revoked' WHERE id ="
        " (SELECT MIN(id) FROM license_keys WHERE status = 'reserved')"
    )
    db.commit()
    assert keys_repo.release_for_order(order_id) == 2
    assert key_states(db) == {"available": 9, "revoked": 1}


def test_release_never_touches_sold_keys(db):
    order_id = place_order(USER, {KEY: 3})
    pay(order_id)
    assert keys_repo.release_for_order(order_id) == 0
    assert key_states(db) == {"available": 7, "sold": 3}


def test_get_sold_keys_returns_this_items_keys_in_order(db):
    order_id = place_order(USER, {KEY: 2})
    pay(order_id)
    (item,) = orders_repo.get_with_items(order_id)[1]
    keys = keys_repo.get_sold_keys(item["id"])
    assert len(keys) == 2 and keys == sorted(keys)
    assert keys_repo.get_sold_keys(999999) == []
    assert keys_repo.count_reserved_for_order(order_id) == 0


# --- orphan reasons, short reservations -------------------------------------------------------


@pytest.mark.parametrize("final", ["expired", "cancelled"])
def test_orphan_reason_for_dead_orders(db, final):
    from bot.repo.orders import OrphanReason

    from .shop import pay_result

    order_id = place_order(USER, {TEXT: 1})
    if final == "expired":
        expire_now(db, order_id)
        orders_repo.expire_due_orders()
    else:
        orders_repo.cancel_order(order_id, "customer")
    result = pay_result(order_id, "ch_r")
    assert result.outcome is PaymentOutcome.ORPHAN
    assert result.reason is OrphanReason.EXPIRED_OR_CANCELLED


@pytest.mark.parametrize("settled", ["paid", "delivered", "refunded"])
def test_orphan_reason_for_an_already_settled_order(db, settled):
    from bot.repo.orders import OrphanReason

    from .shop import pay_result

    order_id = place_order(USER, {TEXT: 1})
    pay_result(order_id, "ch_first")
    if settled in ("delivered", "refunded"):
        orders_repo.set_status(order_id, "delivered", "system")
    if settled == "refunded":
        orders_repo.set_status(order_id, "refunded", "owner")
    result = pay_result(order_id, "ch_second")
    assert result.outcome is PaymentOutcome.ORPHAN
    assert result.reason is OrphanReason.ALREADY_PAID
    assert orders_repo.get(order_id)["telegram_payment_charge_id"] == "ch_first"


def test_only_orphans_carry_a_reason_and_only_short_payments_carry_short_keys(db):
    from .shop import pay_result

    order_id = place_order(USER, {KEY: 2})
    ok = pay_result(order_id, "ch_ok")
    assert (ok.outcome, ok.reason, ok.short_keys) == (PaymentOutcome.PAID, None, None)
    dup = pay_result(order_id, "ch_ok")
    assert (dup.outcome, dup.reason) == (PaymentOutcome.DUPLICATE, None)

    short_order = place_order(OTHER, {KEY: 3})
    db.execute(
        "UPDATE license_keys SET status = 'available', order_item_id = NULL"
        " WHERE id = (SELECT MIN(id) FROM license_keys WHERE status = 'reserved')"
    )
    db.commit()
    short = pay_result(short_order, "ch_short")
    assert short.outcome is PaymentOutcome.PAID and short.short_keys == (2, 3)
