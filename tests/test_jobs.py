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
def clock(monkeypatch):
    """Sweep state is module-level: isolate it, and run on an injectable clock
    (seconds; tests advance it to move past a backoff)."""
    jobs._alerted.clear()
    jobs._retry.clear()
    now = [1000.0]
    monkeypatch.setattr(jobs, "_now", lambda: now[0])
    yield now
    jobs._alerted.clear()
    jobs._retry.clear()


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


async def test_delivery_is_retried_and_succeeds_on_the_next_pass(db, clock):
    order_id = place_order(USER, {TEXT: 1, KEY: 1})
    pay(order_id)
    broken = fake_bot()
    broken.send_message.side_effect = Forbidden("blocked")

    first = await jobs.run_sweep(broken)
    assert first == (1, 0, 0, 1)  # examined 1, one order failed
    assert orders_repo.get(order_id)["status"] == "paid"

    clock[0] += 61  # past the one-minute backoff
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
    assert any("delivery attempt crashed" in r.getMessage() for r in caplog.records)


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


# --- delivery retry backoff (injected clock) ------------------------------------------


def blocked_bot() -> SimpleNamespace:
    bot = fake_bot()

    async def send(**kwargs):
        if kwargs["chat_id"] == USER:
            raise Forbidden("blocked")

    bot.send_message.side_effect = send
    return bot


def attempts(bot) -> int:
    """Delivery attempts the customer's chat saw (each failing send counts)."""
    return len([c for c in bot.send_message.await_args_list if c.kwargs["chat_id"] == USER])


def error_count(caplog) -> int:
    return len(
        [
            r
            for r in caplog.records
            if r.levelno == logging.ERROR and "Delivery failed" in r.getMessage()
        ]
    )


def test_the_backoff_schedule_doubles_and_caps_at_sixty_minutes():
    assert [jobs.backoff_minutes(n) for n in range(1, 10)] == [1, 2, 4, 8, 16, 32, 60, 60, 60]


async def test_a_failing_order_is_retried_on_the_backoff_schedule(db, clock, caplog):
    caplog.set_level(logging.ERROR)
    order_id = place_order(USER, {TEXT: 1})
    pay(order_id)
    bot = blocked_bot()

    await jobs.run_sweep(bot)  # t=0: attempt 1 fails -> next in 1 min
    assert attempts(bot) == 1 and error_count(caplog) == 1

    clock[0] += 30
    skipped = await jobs.run_sweep(bot)  # t=30s: backing off
    clock[0] += 29
    await jobs.run_sweep(bot)  # t=59s: still backing off
    assert attempts(bot) == 1 and error_count(caplog) == 1  # one ERROR per attempt, not per sweep
    assert skipped == (1, 0, 0, 0)  # examined, but not attempted

    clock[0] += 2  # t=61s: past the 1-minute backoff -> attempt 2, next in 2 min
    await jobs.run_sweep(bot)
    assert attempts(bot) == 2 and error_count(caplog) == 2

    clock[0] += 119  # 1m59s after attempt 2
    await jobs.run_sweep(bot)
    assert attempts(bot) == 2
    clock[0] += 2  # past 2 minutes -> attempt 3, next in 4 min
    await jobs.run_sweep(bot)
    assert attempts(bot) == 3 and error_count(caplog) == 3

    clock[0] += 239
    await jobs.run_sweep(bot)
    assert attempts(bot) == 3
    clock[0] += 2  # past 4 minutes -> attempt 4, next in 8 min
    await jobs.run_sweep(bot)
    assert attempts(bot) == 4
    assert jobs._retry[order_id].next_at - clock[0] == 8 * 60


async def test_the_delay_never_exceeds_sixty_minutes(db, clock):
    for _ in range(12):
        jobs._record_failure(7)
    assert jobs._retry[7].next_at - clock[0] == 60 * 60
    assert jobs._retry[7].failures == 12


async def test_a_successful_retry_clears_the_backoff(db, clock):
    order_id = place_order(USER, {TEXT: 1})
    pay(order_id)
    await jobs.run_sweep(blocked_bot())
    assert order_id in jobs._retry

    clock[0] += 61
    report = await jobs.run_sweep(fake_bot())
    assert report.delivered == 1
    assert order_id not in jobs._retry
    assert orders_repo.get(order_id)["status"] == "delivered"


async def test_backoff_state_for_orders_that_left_the_queue_is_forgotten(db, clock):
    jobs._retry[424242] = jobs._Retry(3, clock[0] + 999)
    await jobs.run_sweep(fake_bot())
    assert 424242 not in jobs._retry


async def test_retry_delivery_now_bypasses_and_resets_the_backoff(db, clock, caplog):
    caplog.set_level(logging.ERROR)
    order_id = place_order(USER, {TEXT: 1})
    pay(order_id)
    bot = blocked_bot()
    for _ in range(3):  # fail three times, driving the delay up to 4 minutes
        await jobs.run_sweep(bot)
        clock[0] += 60 * 60
    assert jobs._retry[order_id].failures == 3
    clock[0] += 0
    jobs._retry[order_id].next_at = clock[0] + 4 * 60  # mid-backoff

    before = attempts(bot)
    result = await jobs.retry_delivery_now(bot, order_id)  # not waiting for the schedule

    assert attempts(bot) == before + 1  # it really attempted, ignoring the backoff
    assert len(result.failed) == 1
    state = jobs._retry[order_id]
    assert state.failures == 1  # reset, then this failure counted afresh
    assert state.next_at - clock[0] == 60  # next automatic retry in one minute, not eight


async def test_retry_delivery_now_clears_the_backoff_on_success(db, clock):
    order_id = place_order(USER, {TEXT: 1})
    pay(order_id)
    await jobs.run_sweep(blocked_bot())
    assert order_id in jobs._retry

    result = await jobs.retry_delivery_now(fake_bot(), order_id)

    assert len(result.delivered) == 1 and result.failed == []
    assert order_id not in jobs._retry
    assert orders_repo.get(order_id)["status"] == "delivered"


async def test_retry_delivery_now_survives_a_crash_and_starts_the_backoff(db, clock, monkeypatch):
    order_id = place_order(USER, {TEXT: 1})
    pay(order_id)

    async def crash(bot, order_id):
        raise RuntimeError("delivery bug")

    monkeypatch.setattr(delivery, "deliver_order", crash)
    result = await jobs.retry_delivery_now(fake_bot(), order_id)
    assert result == ([], [])
    assert jobs._retry[order_id].failures == 1


async def test_sweep_command_ignores_the_backoff_because_the_owner_asked(db, clock):
    add_customer(OWNER_ID)
    order_id = place_order(USER, {TEXT: 1})
    pay(order_id)
    await jobs.run_sweep(blocked_bot())  # puts the order into backoff
    assert order_id in jobs._retry
    context = make_context()
    message = make_message("/sweep")

    await jobs.sweep_command(make_update(OWNER_ID, message=message), context)

    assert orders_repo.get(order_id)["status"] == "delivered"  # attempted despite the backoff
    assert "delivered 1" in message.reply_text.await_args.args[0]


async def test_the_stuck_order_alert_still_fires_while_backing_off(db, clock):
    order_id = place_order(USER, {TEXT: 1})
    pay(order_id)
    bot = blocked_bot()
    await jobs.run_sweep(bot)  # fails -> backoff
    age_payment(db, order_id, 45)
    await jobs.run_sweep(bot)  # skipped for backoff, but the alert is independent
    assert len([m for m in texts_to(bot, OWNER_ID) if "still undelivered" in m]) == 1
