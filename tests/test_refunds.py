"""bot/refunds.py: every status, ordering, provider choice and the guard."""

from __future__ import annotations

import asyncio
import logging
import re
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from telegram.error import BadRequest

from bot import refunds
from bot.config import get_settings
from bot.refunds import RefundResult, RefundStatus, refund_order
from bot.repo import license_keys as keys_repo
from bot.repo import orders as orders_repo

from .shop import KEY, TEXT, key_states, pay, place_order

USER = 3001


def fake_bot() -> Any:
    return SimpleNamespace(refund_star_payment=AsyncMock(return_value=True))


def snapshot(order_id: int) -> tuple[Any, ...]:
    order = orders_repo.get(order_id)
    assert order is not None
    return dict(order), len(orders_repo.list_events(order_id))


def paid_order(lines=None, mode="stars", charge="ch_1", user=USER) -> int:
    order_id = place_order(user, lines or {KEY: 3}, mode=mode)
    pay(order_id, charge)
    return order_id


BOT_TOKEN_SHAPE = re.compile(r"\d{8,10}:[A-Za-z0-9_-]{35}")


@pytest.fixture(autouse=True)
def clean_guard(caplog):
    """Every refund-flow test also proves no token-shaped string reaches the logs."""
    caplog.set_level(logging.DEBUG)
    refunds._in_flight.clear()
    yield
    refunds._in_flight.clear()
    assert not BOT_TOKEN_SHAPE.search(caplog.text)


# --- the happy path ---------------------------------------------------------------------------


async def test_a_stars_order_is_refunded_with_its_own_charge_id_and_keys_are_revoked(db):
    order_id = paid_order(charge="ch_abc")
    bot = fake_bot()

    result = await refund_order(bot, order_id, "owner")

    assert result == RefundResult(RefundStatus.REFUNDED, "3 keys revoked")
    bot.refund_star_payment.assert_awaited_once_with(
        user_id=USER, telegram_payment_charge_id="ch_abc"
    )
    order = orders_repo.get(order_id)
    assert order["status"] == "refunded" and order["refunded_at"] is not None
    assert key_states(db) == {"available": 7, "revoked": 3}
    event = orders_repo.list_events(order_id)[-1]
    assert (event["to_status"], event["actor"]) == ("refunded", "owner")


async def test_a_delivered_order_can_be_refunded(db):
    order_id = paid_order({TEXT: 1})
    orders_repo.set_status(order_id, "delivered", "system")
    assert (await refund_order(fake_bot(), order_id, "owner")).status is RefundStatus.REFUNDED


async def test_refunded_keys_are_never_available_again(db):
    order_id = paid_order({KEY: 10})
    await refund_order(fake_bot(), order_id, "owner")
    assert keys_repo.count_available(KEY) == 0
    assert keys_repo.count_by_status(KEY) == {
        "available": 0,
        "reserved": 0,
        "sold": 0,
        "revoked": 10,
    }
    assert keys_repo.release_for_order(order_id) == 0  # revoked keys cannot be released either


# --- provider choice follows the ORDER's mode -------------------------------------------------


async def test_a_stars_order_calls_the_real_refund_even_when_the_bot_runs_in_mock_mode(
    db, monkeypatch
):
    order_id = paid_order(mode="stars", charge="ch_stars_order")
    monkeypatch.setenv("PAYMENT_MODE", "mock")
    get_settings.cache_clear()
    assert get_settings().payment_mode == "mock"
    bot = fake_bot()

    result = await refund_order(bot, order_id, "owner")

    assert result.status is RefundStatus.REFUNDED
    bot.refund_star_payment.assert_awaited_once_with(
        user_id=USER, telegram_payment_charge_id="ch_stars_order"
    )


async def test_a_mock_order_never_reaches_telegram_even_when_the_bot_runs_in_stars_mode(
    db, monkeypatch
):
    order_id = paid_order(mode="mock", charge="mock_deadbeef")
    monkeypatch.setenv("PAYMENT_MODE", "stars")
    get_settings.cache_clear()
    assert get_settings().payment_mode == "stars"
    bot = fake_bot()

    result = await refund_order(bot, order_id, "owner")

    assert result.status is RefundStatus.REFUNDED
    bot.refund_star_payment.assert_not_called()
    assert orders_repo.get(order_id)["status"] == "refunded"


async def test_a_mock_order_without_a_charge_id_is_still_refundable(db):
    order_id = place_order(USER, {TEXT: 1}, mode="mock")
    orders_repo.set_status(order_id, "paid", "system")  # no charge id recorded
    bot = fake_bot()
    assert (await refund_order(bot, order_id, "owner")).status is RefundStatus.REFUNDED
    bot.refund_star_payment.assert_not_called()


# --- the other statuses -----------------------------------------------------------------------


async def test_not_found(db):
    bot = fake_bot()
    assert await refund_order(bot, 99999, "owner") == RefundResult(RefundStatus.NOT_FOUND)
    bot.refund_star_payment.assert_not_called()


async def test_already_refunded_and_a_double_call_gives_exactly_one_provider_call(db):
    order_id = paid_order()
    bot = fake_bot()
    first = await refund_order(bot, order_id, "owner")
    second = await refund_order(bot, order_id, "owner")
    assert first.status is RefundStatus.REFUNDED
    assert second == RefundResult(RefundStatus.ALREADY_REFUNDED)
    assert bot.refund_star_payment.await_count == 1


@pytest.mark.parametrize("state", ["pending_payment", "cancelled", "expired"])
async def test_orders_that_were_never_paid_are_not_refundable(db, state):
    order_id = place_order(USER, {KEY: 2})
    if state == "cancelled":
        orders_repo.cancel_order(order_id, "customer")
    elif state == "expired":
        orders_repo.set_status(order_id, "expired", "system")
    bot = fake_bot()
    before = (snapshot(order_id), key_states(db))

    result = await refund_order(bot, order_id, "owner")

    assert result.status is RefundStatus.NOT_REFUNDABLE and state in result.detail
    bot.refund_star_payment.assert_not_called()
    assert (snapshot(order_id), key_states(db)) == before


async def test_a_stars_order_without_a_charge_id_cannot_be_refunded(db):
    order_id = place_order(USER, {KEY: 2}, mode="stars")
    orders_repo.set_status(order_id, "paid", "system")  # paid, but no charge id stored
    bot = fake_bot()
    before = snapshot(order_id)

    assert await refund_order(bot, order_id, "owner") == RefundResult(RefundStatus.NO_CHARGE_ID)

    bot.refund_star_payment.assert_not_called()
    assert snapshot(order_id) == before


# --- the provider fails first: nothing changes ------------------------------------------------


@pytest.mark.parametrize("failure", ["false", "telegram_error"])
async def test_a_failed_provider_refund_changes_nothing_in_the_database(db, failure, caplog):
    caplog.set_level(logging.ERROR)
    order_id = paid_order()
    bot = fake_bot()
    if failure == "false":
        bot.refund_star_payment.return_value = False
    else:
        bot.refund_star_payment.side_effect = BadRequest("CHARGE_ALREADY_REFUNDED")
    before = (snapshot(order_id), key_states(db))

    result = await refund_order(bot, order_id, "owner")

    assert result.status is RefundStatus.PROVIDER_FAILED and result.detail
    assert (snapshot(order_id), key_states(db)) == before
    assert any(r.levelno == logging.ERROR for r in caplog.records)


async def test_a_provider_that_raises_is_provider_failed_with_a_short_detail(db, monkeypatch):
    order_id = paid_order()
    provider = SimpleNamespace(
        mode="stars", refund=AsyncMock(side_effect=RuntimeError("boom " + "x" * 500))
    )
    monkeypatch.setattr(refunds, "build_provider", lambda mode: provider)
    before = (snapshot(order_id), key_states(db))

    result = await refund_order(fake_bot(), order_id, "owner")

    assert result.status is RefundStatus.PROVIDER_FAILED
    assert result.detail.startswith("RuntimeError: boom") and len(result.detail) <= 120
    assert (snapshot(order_id), key_states(db)) == before


# --- the database fails AFTER the money went back ---------------------------------------------


async def test_a_database_failure_after_a_successful_refund_is_reported_for_manual_reconciliation(
    db, monkeypatch, caplog
):
    caplog.set_level(logging.ERROR)
    order_id = paid_order(charge="ch_reconcile")

    def boom(order_id, actor, note=None):
        raise RuntimeError("disk full")

    monkeypatch.setattr(orders_repo, "mark_refunded", boom)
    bot = fake_bot()

    result = await refund_order(bot, order_id, "owner")

    assert result.status is RefundStatus.DB_FAILED_AFTER_REFUND
    bot.refund_star_payment.assert_awaited_once()  # the money did go back
    assert orders_repo.get(order_id)["status"] == "paid"  # but our records did not move
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert any(f"order_id={order_id}" in m and "charge_id=ch_reconcile" in m for m in errors)
    assert not any("SimpleNamespace" in m or "Update(" in m for m in errors)


async def test_a_forced_failure_inside_mark_refunded_rolls_status_and_keys_back_together(
    db, monkeypatch
):
    order_id = paid_order()
    keys_before = key_states(db)
    monkeypatch.setattr(
        keys_repo, "revoke_for_order_in", lambda conn, oid: (_ for _ in ()).throw(OSError("x"))
    )

    result = await refund_order(fake_bot(), order_id, "owner")

    assert result.status is RefundStatus.DB_FAILED_AFTER_REFUND
    order = orders_repo.get(order_id)
    assert order["status"] == "paid" and order["refunded_at"] is None
    assert key_states(db) == keys_before  # sold keys were not revoked either


async def test_losing_a_race_to_another_refund_after_the_provider_call_is_db_failed(
    db, monkeypatch
):
    order_id = paid_order()
    real_provider_refund = refunds.build_provider("stars").refund

    async def refund_then_lose_the_race(bot, user_id, charge_id):
        result = await real_provider_refund(bot, user_id, charge_id)
        orders_repo.mark_refunded(order_id, "someone-else")  # another path got there first
        return result

    provider = SimpleNamespace(mode="stars", refund=refund_then_lose_the_race)
    monkeypatch.setattr(refunds, "build_provider", lambda mode: provider)

    result = await refund_order(fake_bot(), order_id, "owner")
    assert result.status is RefundStatus.DB_FAILED_AFTER_REFUND


# --- the in-flight guard ----------------------------------------------------------------------


async def test_overlapping_refunds_call_the_provider_once(db):
    order_id = paid_order()
    bot = fake_bot()
    gate = asyncio.Event()
    entered = asyncio.Event()

    async def slow_refund(**kwargs):
        entered.set()
        await gate.wait()
        return True

    bot.refund_star_payment.side_effect = slow_refund

    first = asyncio.ensure_future(refund_order(bot, order_id, "owner"))
    await asyncio.wait_for(entered.wait(), timeout=2)
    second = await refund_order(bot, order_id, "owner")  # the double tap

    assert second == RefundResult(RefundStatus.IN_FLIGHT)
    assert bot.refund_star_payment.await_count == 1
    gate.set()
    assert (await first).status is RefundStatus.REFUNDED
    assert bot.refund_star_payment.await_count == 1
    assert order_id not in refunds._in_flight


async def test_the_guard_is_released_after_failures_and_exceptions(db, monkeypatch):
    order_id = paid_order()
    bot = fake_bot()
    bot.refund_star_payment.return_value = False
    await refund_order(bot, order_id, "owner")
    assert order_id not in refunds._in_flight

    async def exploding(bot, order_id, actor):
        raise RuntimeError("unexpected")

    with monkeypatch.context() as patch:
        patch.setattr(refunds, "_refund_order", exploding)
        with pytest.raises(RuntimeError):
            await refund_order(bot, order_id, "owner")
    assert order_id not in refunds._in_flight

    bot.refund_star_payment.return_value = True  # a later attempt works
    assert (await refund_order(bot, order_id, "owner")).status is RefundStatus.REFUNDED


async def test_the_guard_is_per_order(db):
    first = paid_order(charge="ch_a", user=3001)
    second = paid_order(charge="ch_b", user=3002)
    bot = fake_bot()
    gate = asyncio.Event()

    async def slow(**kwargs):
        await gate.wait()
        return True

    bot.refund_star_payment.side_effect = slow
    one = asyncio.ensure_future(refund_order(bot, first, "owner"))
    await asyncio.sleep(0)
    two = asyncio.ensure_future(refund_order(bot, second, "owner"))
    await asyncio.sleep(0.01)
    assert refunds._in_flight == {first, second}
    gate.set()
    results = await asyncio.gather(one, two)
    assert all(r.status is RefundStatus.REFUNDED for r in results)
