"""The recurring sweep and /sweep."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.error import Forbidden

import bot.jobs as jobs
from bot import delivery
from bot.repo import orders as orders_repo

from .conftest import OWNER_ID
from .helpers import add_customer, make_context, make_message, make_update
from .shop import KEY, TEXT, expire_now, key_states, pay, place_order

USER = 3001
OTHER = 3002


@pytest.fixture(autouse=True)
def clean_alerts():
    jobs._alerted.clear()
    yield
    jobs._alerted.clear()


def fake_bot() -> SimpleNamespace:
    return SimpleNamespace(send_message=AsyncMock(), send_document=AsyncMock())


def texts_to(bot, chat_id: int) -> list[str]:
    return [
        str(c.kwargs["text"])
        for c in bot.send_message.await_args_list
        if c.kwargs["chat_id"] == chat_id
    ]


def age_payment(db, order_id: int, minutes: int) -> None:
    db.execute(
        "UPDATE orders SET paid_at = datetime('now', ?) WHERE id = ?",
        (f"-{minutes} minutes", order_id),
    )
    db.commit()


# --- expiry -----------------------------------------------------------------------------------


async def test_the_sweep_expires_releases_and_notifies_once(db):
    order_id = place_order(USER, {KEY: 3})
    expire_now(db, order_id)
    bot = fake_bot()

    report = await jobs.run_sweep(bot)

    assert report == (1, 1, 0, 0)
    assert orders_repo.get(order_id)["status"] == "expired"
    assert key_states(db) == {"available": 10}
    (notice,) = texts_to(bot, USER)
    assert f"order #{order_id} expired" in notice
    markup = bot.send_message.await_args_list[0].kwargs["reply_markup"]
    assert [b.text for row in markup.inline_keyboard for b in row] == ["Browse products"]

    again = await jobs.run_sweep(bot)  # nothing left to expire, so no second notice
    assert again == (0, 0, 0, 0)
    assert len(texts_to(bot, USER)) == 1


async def test_orders_still_inside_their_window_are_left_alone(db):
    order_id = place_order(USER, {KEY: 1})
    bot = fake_bot()
    assert (await jobs.run_sweep(bot)).expired == 0
    assert orders_repo.get(order_id)["status"] == "pending_payment"
    bot.send_message.assert_not_called()


async def test_a_failing_notification_does_not_stop_the_batch(db, caplog):
    caplog.set_level(logging.ERROR)
    first = place_order(USER, {KEY: 1})
    second = place_order(OTHER, {KEY: 1})
    expire_now(db, first)
    expire_now(db, second)
    bot = fake_bot()

    async def send(**kwargs):
        if kwargs["chat_id"] == USER:
            raise Forbidden("blocked")

    bot.send_message.side_effect = send
    report = await jobs.run_sweep(bot)

    assert report.expired == 2
    assert orders_repo.get(first)["status"] == "expired"
    assert orders_repo.get(second)["status"] == "expired"
    assert any(r.levelno == logging.ERROR for r in caplog.records)
    assert len(texts_to(bot, OTHER)) == 1  # the second customer was still told


async def test_a_notification_helper_that_raises_is_isolated_too(db, monkeypatch, caplog):
    caplog.set_level(logging.ERROR)
    first, second = place_order(USER, {KEY: 1}), place_order(OTHER, {KEY: 1})
    expire_now(db, first)
    expire_now(db, second)
    calls = []

    async def exploding(bot, order_id, user_id):
        calls.append(order_id)
        raise RuntimeError("bug in the notifier")

    monkeypatch.setattr(jobs, "_notify_expired", exploding)
    report = await jobs.run_sweep(fake_bot())
    assert report.expired == 2 and sorted(calls) == sorted([first, second])
    assert sum("expiry notice failed" in r.getMessage() for r in caplog.records) == 2


# --- delivery retries -------------------------------------------------------------------------


async def test_delivery_is_retried_and_succeeds_on_the_next_pass(db):
    order_id = place_order(USER, {TEXT: 1, KEY: 1})
    pay(order_id)
    broken = fake_bot()
    broken.send_message.side_effect = Forbidden("blocked")

    first = await jobs.run_sweep(broken)
    assert first == (1, 0, 0, 1)  # examined 1, one order failed
    assert orders_repo.get(order_id)["status"] == "paid"

    healthy = fake_bot()
    second = await jobs.run_sweep(healthy)
    assert second == (1, 0, 1, 0)
    assert orders_repo.get(order_id)["status"] == "delivered"
    assert any("example.com/demo" in t for t in texts_to(healthy, USER))

    third = await jobs.run_sweep(healthy)
    assert third == (0, 0, 0, 0)  # delivered orders are no longer examined


async def test_one_orders_delivery_crash_does_not_stop_the_others(db, monkeypatch, caplog):
    caplog.set_level(logging.ERROR)
    first = place_order(USER, {TEXT: 1})
    second = place_order(OTHER, {TEXT: 1})
    pay(first)
    pay(second)
    real = delivery.deliver_order

    async def flaky(bot, order_id):
        if order_id == first:
            raise RuntimeError("delivery bug")
        return await real(bot, order_id)

    monkeypatch.setattr(delivery, "deliver_order", flaky)
    report = await jobs.run_sweep(fake_bot())
    assert report.delivered == 1 and report.failed == 1
    assert orders_repo.get(second)["status"] == "delivered"
    assert any("delivery retry failed" in r.getMessage() for r in caplog.records)


# --- stuck-order owner alerts -----------------------------------------------------------------


async def test_the_owner_is_alerted_once_about_an_order_stuck_for_over_30_minutes(db):
    order_id = place_order(USER, {TEXT: 1})
    pay(order_id)
    age_payment(db, order_id, 31)
    bot = fake_bot()
    bot.send_message.side_effect = Forbidden("customer blocked")  # keeps it undelivered

    async def send(**kwargs):
        if kwargs["chat_id"] == USER:
            raise Forbidden("blocked")

    bot.send_message.side_effect = send
    await jobs.run_sweep(bot)
    await jobs.run_sweep(bot)

    alerts = texts_to(bot, OWNER_ID)
    assert len(alerts) == 1
    assert f"Order #{order_id} is paid but still undelivered" in alerts[0]


async def test_a_fresh_stuck_order_does_not_alert_yet(db):
    order_id = place_order(USER, {TEXT: 1})
    pay(order_id)
    age_payment(db, order_id, 5)
    bot = fake_bot()

    async def send(**kwargs):
        if kwargs["chat_id"] == USER:
            raise Forbidden("blocked")

    bot.send_message.side_effect = send
    await jobs.run_sweep(bot)
    assert texts_to(bot, OWNER_ID) == []


async def test_an_owner_alert_failure_is_isolated(db, caplog):
    caplog.set_level(logging.ERROR)
    order_id = place_order(USER, {TEXT: 1})
    pay(order_id)
    age_payment(db, order_id, 45)
    bot = fake_bot()
    bot.send_message.side_effect = Forbidden("everyone blocked us")
    report = await jobs.run_sweep(bot)  # must not raise
    assert report.failed == 1


# --- the job wrapper and /sweep ---------------------------------------------------------------


async def test_the_recurring_job_never_raises(db, monkeypatch, caplog):
    caplog.set_level(logging.ERROR)

    async def crash(bot):
        raise RuntimeError("whole pass crashed")

    monkeypatch.setattr(jobs, "run_sweep", crash)
    await jobs.sweep_job(make_context())  # no exception escapes
    assert any("Sweep pass crashed" in r.getMessage() for r in caplog.records)


async def test_a_pass_survives_failures_in_each_stage(db, monkeypatch, caplog):
    caplog.set_level(logging.ERROR)
    monkeypatch.setattr(
        orders_repo, "expire_due_orders", lambda: (_ for _ in ()).throw(RuntimeError("expiry"))
    )
    monkeypatch.setattr(
        orders_repo,
        "list_paid_undelivered_orders",
        lambda: (_ for _ in ()).throw(RuntimeError("listing")),
    )
    assert await jobs.run_sweep(fake_bot()) == (0, 0, 0, 0)
    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "expiry pass failed" in messages and "could not list paid orders" in messages


async def test_sweep_command_runs_a_pass_and_reports_counts_for_the_owner(db):
    add_customer(OWNER_ID)
    expired = place_order(USER, {KEY: 1})
    expire_now(db, expired)
    paid = place_order(OTHER, {TEXT: 1})
    pay(paid)
    context = make_context()
    message = make_message("/sweep")

    await jobs.sweep_command(make_update(OWNER_ID, message=message), context)

    (reply,) = [c.args[0] for c in message.reply_text.await_args_list]
    assert reply == "Sweep done: examined 2, expired 1, delivered 1, failed 0."
    assert orders_repo.get(paid)["status"] == "delivered"


async def test_sweep_command_refuses_non_owners_and_does_nothing(db):
    expired = place_order(USER, {KEY: 1})
    expire_now(db, expired)
    context = make_context()
    message = make_message("/sweep")

    await jobs.sweep_command(make_update(OTHER, message=message), context)

    assert "not authorized" in message.reply_text.await_args.args[0]
    assert orders_repo.get(expired)["status"] == "pending_payment"  # no pass ran
    context.bot.send_message.assert_not_called()
