"""The shared payability check (bot/payments/checks.py), reason by reason."""

from __future__ import annotations

import pytest

from bot.i18n import STRINGS
from bot.payments.checks import REJECTION_MESSAGE_KEY, PayRejection, check_payable
from bot.repo import orders as orders_repo

from .shop import KEY, TEXT, expire_now, place_order

USER = 3001
OTHER = 3002


def check(order_id, **overrides):
    order = orders_repo.get(order_id)
    args = {
        "payer_id": USER,
        "amount": order["total_stars"] if order else 0,
        "currency": "XTR",
        "mode": "stars",
        **overrides,
    }
    return check_payable(order, **args)


def test_a_valid_order_passes(db):
    assert check(place_order(USER, {TEXT: 1, KEY: 2})) is None


def test_a_missing_order(db):
    assert check_payable(None, USER, 1, "XTR", "stars") is PayRejection.NOT_FOUND


def test_someone_elses_order(db):
    assert check(place_order(USER), payer_id=OTHER) is PayRejection.NOT_OWNER


def test_a_settled_order(db):
    order_id = place_order(USER)
    orders_repo.cancel_order(order_id, "customer")
    assert check(order_id) is PayRejection.NOT_PENDING


def test_an_expired_but_unswept_order(db):
    order_id = place_order(USER)
    expire_now(db, order_id)
    assert orders_repo.get(order_id)["status"] == "pending_payment"
    assert check(order_id) is PayRejection.EXPIRED


def test_the_payment_mode_must_match_the_running_mode(db):
    order_id = place_order(USER, mode="mock")
    assert check(order_id, mode="stars") is PayRejection.WRONG_MODE
    assert check(order_id, mode="mock") is None


def test_currency_is_checked_in_stars_mode_only(db):
    stars_order = place_order(USER, mode="stars")
    assert check(stars_order, currency="USD") is PayRejection.WRONG_CURRENCY
    orders_repo.cancel_order(stars_order, "customer")
    mock_order = place_order(USER, mode="mock")
    assert check(mock_order, currency="USD", mode="mock") is None


@pytest.mark.parametrize("delta", [-1, 1])
def test_the_amount_must_equal_the_total(db, delta):
    order_id = place_order(USER)
    total = orders_repo.get(order_id)["total_stars"]
    assert check(order_id, amount=total + delta) is PayRejection.WRONG_AMOUNT


def test_the_key_reservation_must_be_intact(db):
    order_id = place_order(USER, {KEY: 3})
    assert check(order_id) is None
    db.execute(
        "UPDATE license_keys SET status = 'available', order_item_id = NULL"
        " WHERE id = (SELECT MIN(id) FROM license_keys WHERE status = 'reserved')"
    )
    db.commit()
    assert check(order_id) is PayRejection.RESERVATION_LOST


def test_orders_without_key_lines_need_no_reservation(db):
    assert check(place_order(USER, {TEXT: 1})) is None


def test_every_rejection_has_a_translated_message():
    assert set(REJECTION_MESSAGE_KEY) == set(PayRejection)
    for key in REJECTION_MESSAGE_KEY.values():
        assert set(STRINGS[key]) == {"en", "ru"}


def test_missing_and_foreign_orders_share_one_message():
    assert (
        REJECTION_MESSAGE_KEY[PayRejection.NOT_FOUND]
        == REJECTION_MESSAGE_KEY[PayRejection.NOT_OWNER]
    )
