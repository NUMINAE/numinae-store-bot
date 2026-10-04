"""PerUserUpdateProcessor: different users run concurrently, one user's updates stay
strictly ordered, the per-user cap drops floods, and payments are never dropped."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

from bot import main
from bot.concurrency import (
    DEFAULT_MAX_CONCURRENT_UPDATES,
    DEFAULT_MAX_PER_USER,
    PerUserUpdateProcessor,
)
from bot.handlers import admin_catalogue, checkout, payments
from bot.repo import orders as orders_repo

from .conftest import OWNER_ID
from .helpers import add_customer, make_callback_query, make_update
from .shop import KEY, TEXT, fill_cart, key_states, place_order
from .test_payment_handlers import pre_checkout_query, stars_context

A, B, FLOODER = 7001, 7002, 7003


def message_update(user_id: int):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id), pre_checkout_query=None, message=None
    )


def pre_checkout_update(user_id: int):
    update = message_update(user_id)
    update.pre_checkout_query = SimpleNamespace(from_user=SimpleNamespace(id=user_id))
    return update


def payment_update(user_id: int):
    update = message_update(user_id)
    update.message = SimpleNamespace(successful_payment=SimpleNamespace())
    return update


def userless_update():
    return SimpleNamespace(effective_user=None, pre_checkout_query=None, message=None)


async def spin(times: int = 5) -> None:
    for _ in range(times):
        await asyncio.sleep(0)


class Recorder:
    """Coroutines that log when they start and finish, and can be held open."""

    def __init__(self) -> None:
        self.log: list[str] = []
        self.gates: dict[str, asyncio.Event] = {}

    def gate(self, name: str) -> asyncio.Event:
        return self.gates.setdefault(name, asyncio.Event())

    async def run(self, name: str, *, hold: bool = False, boom: bool = False) -> None:
        self.log.append(f"start:{name}")
        if hold:
            await self.gate(name).wait()
        if boom:
            raise RuntimeError(f"{name} failed")
        self.log.append(f"end:{name}")


def submit(processor, update, coroutine) -> asyncio.Task:
    return asyncio.ensure_future(processor.process_update(update, coroutine))


# --- defaults and wiring ----------------------------------------------------------------------


def test_defaults_are_256_slots_and_8_per_user():
    assert (DEFAULT_MAX_CONCURRENT_UPDATES, DEFAULT_MAX_PER_USER) == (256, 8)
    processor = PerUserUpdateProcessor()
    assert processor.max_concurrent_updates == 256


def test_the_application_uses_the_per_user_processor(db):
    application = main.build_application()
    assert isinstance(application.update_processor, PerUserUpdateProcessor)
    assert application.concurrent_updates == 256


@pytest.mark.parametrize("bad", [0, -1])
def test_invalid_limits_are_rejected(bad):
    with pytest.raises(ValueError):
        PerUserUpdateProcessor(max_per_user=bad)
    with pytest.raises(ValueError):
        PerUserUpdateProcessor(max_concurrent_updates=bad)


# --- B3 1: different users run concurrently ----------------------------------------------------


async def test_another_users_update_is_processed_while_a_users_handler_is_blocked():
    processor = PerUserUpdateProcessor()
    rec = Recorder()
    blocked = submit(processor, message_update(A), rec.run("a1", hold=True))
    await spin()
    other = submit(processor, message_update(B), rec.run("b1"))
    await asyncio.wait_for(other, timeout=1)  # finished although A is still blocked

    assert rec.log == ["start:a1", "start:b1", "end:b1"]
    assert not blocked.done()
    rec.gate("a1").set()
    await asyncio.wait_for(blocked, timeout=1)


async def test_updates_without_a_user_are_processed_without_a_lock():
    processor = PerUserUpdateProcessor()
    rec = Recorder()
    await processor.process_update(userless_update(), rec.run("x"))
    assert rec.log == ["start:x", "end:x"] and processor.tracked_users == 0


# --- B3 2 / d: strict per-user ordering ---------------------------------------------------------


async def test_two_updates_from_the_same_user_are_strictly_ordered():
    processor = PerUserUpdateProcessor()
    rec = Recorder()
    first = submit(processor, message_update(A), rec.run("first", hold=True))
    await spin()
    second = submit(processor, message_update(A), rec.run("second"))
    await spin(20)

    assert rec.log == ["start:first"]  # the second has not started
    rec.gate("first").set()
    await asyncio.wait_for(asyncio.gather(first, second), timeout=1)
    assert rec.log == ["start:first", "end:first", "start:second", "end:second"]


async def test_a_successful_payment_waits_for_the_users_earlier_update_then_is_processed():
    processor = PerUserUpdateProcessor()
    rec = Recorder()
    earlier = submit(processor, message_update(A), rec.run("earlier", hold=True))
    await spin()
    payment = submit(processor, payment_update(A), rec.run("payment"))
    later = submit(processor, message_update(A), rec.run("cancel-tap"))
    await spin(20)
    assert rec.log == ["start:earlier"]  # the payment waits its turn (inside the lock)

    rec.gate("earlier").set()
    await asyncio.wait_for(asyncio.gather(earlier, payment, later), timeout=1)
    assert rec.log == [
        "start:earlier",
        "end:earlier",
        "start:payment",
        "end:payment",
        "start:cancel-tap",  # a later update of theirs only after the payment is recorded
        "end:cancel-tap",
    ]


# --- B3 3: the pool size is respected -----------------------------------------------------------


async def test_the_pool_limit_is_respected_the_seventeenth_update_waits():
    processor = PerUserUpdateProcessor(max_concurrent_updates=16)
    rec = Recorder()
    tasks = [
        submit(processor, message_update(1000 + n), rec.run(f"u{n}", hold=True)) for n in range(17)
    ]
    await spin(20)
    started = [e for e in rec.log if e.startswith("start:")]
    assert len(started) == 16 and "start:u16" not in rec.log  # the 17th waits for a slot
    assert processor.current_concurrent_updates == 16

    rec.gate("u0").set()
    await spin(20)
    assert "start:u16" in rec.log  # a slot freed, the 17th ran
    for n in range(17):
        rec.gate(f"u{n}").set()
    await asyncio.wait_for(asyncio.gather(*tasks), timeout=2)


# --- B3 4 / e: the lock map and the counts are released ----------------------------------------


async def test_the_lock_map_is_empty_after_updates_complete():
    processor = PerUserUpdateProcessor()
    rec = Recorder()
    await asyncio.gather(
        *[processor.process_update(message_update(2000 + n), rec.run(f"u{n}")) for n in range(20)]
    )
    assert processor.tracked_users == 0


async def test_after_an_exception_the_lock_entry_and_the_count_are_released():
    processor = PerUserUpdateProcessor()
    rec = Recorder()
    with pytest.raises(RuntimeError):
        await processor.process_update(message_update(A), rec.run("bad", boom=True))
    assert processor.tracked_users == 0 and processor.pending_for(A) == 0

    # and the user is not wedged: their next update runs normally
    await asyncio.wait_for(processor.process_update(message_update(A), rec.run("next")), 1)
    assert rec.log[-2:] == ["start:next", "end:next"]


async def test_an_exception_does_not_stop_queued_updates_of_the_same_user():
    processor = PerUserUpdateProcessor()
    rec = Recorder()
    first = submit(processor, message_update(A), rec.run("bad", hold=True, boom=True))
    await spin()
    second = submit(processor, message_update(A), rec.run("good"))
    await spin()
    rec.gate("bad").set()
    results = await asyncio.wait_for(asyncio.gather(first, second, return_exceptions=True), 1)
    assert isinstance(results[0], RuntimeError) and results[1] is None
    assert "end:good" in rec.log
    assert processor.tracked_users == 0 and processor.pending_for(A) == 0


async def test_a_cancelled_update_releases_its_count():
    processor = PerUserUpdateProcessor()
    rec = Recorder()
    task = submit(processor, message_update(A), rec.run("held", hold=True))
    await spin()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert processor.tracked_users == 0


# --- a: a flood is capped, others are unaffected -------------------------------------------------


async def test_a_flood_of_50_is_capped_at_8_and_another_user_is_still_processed_promptly(caplog):
    caplog.set_level(logging.INFO, logger="bot.concurrency")
    processor = PerUserUpdateProcessor()
    rec = Recorder()
    flood = [
        submit(processor, message_update(FLOODER), rec.run(f"f{n}", hold=(n == 0)))
        for n in range(50)
    ]
    await spin(30)
    other = submit(processor, message_update(B), rec.run("b1"))
    await asyncio.wait_for(other, timeout=1)  # not stuck behind the flooder

    assert rec.log.count("start:f0") == 1 and "end:b1" in rec.log
    assert processor.pending_for(FLOODER) == 8  # the first plus seven queued; 42 dropped
    dropped = [r for r in caplog.records if "Dropped" in r.getMessage()]
    assert len(dropped) == 1  # 42 drops, one line: not one line per dropped update
    assert dropped[0].levelno == logging.INFO
    assert dropped[0].getMessage() == (
        f"Dropped 1 update(s) from user {FLOODER} (over the per-user limit) since the last report"
    )  # the user id and a count, nothing else

    rec.gate("f0").set()
    await asyncio.wait_for(asyncio.gather(*flood), timeout=2)
    ran = [e for e in rec.log if e.startswith("end:f")]
    assert len(ran) == 8  # exactly the admitted ones ran; the dropped never started
    assert processor.tracked_users == 0


async def test_dropped_updates_leave_no_never_awaited_warning(recwarn):
    processor = PerUserUpdateProcessor(max_per_user=1)
    rec = Recorder()
    first = submit(processor, message_update(A), rec.run("a", hold=True))
    await spin()
    try:
        # Over the cap: returns at once. Without the cap it would wait on the lock.
        await asyncio.wait_for(processor.process_update(message_update(A), rec.run("dropped")), 1)
    finally:
        rec.gate("a").set()
    await first
    import gc

    gc.collect()
    assert not [w for w in recwarn if "never awaited" in str(w.message)]


# --- b: payments are never dropped ---------------------------------------------------------------


async def test_a_flooders_pre_checkout_and_successful_payment_are_processed_not_dropped(caplog):
    caplog.set_level(logging.INFO, logger="bot.concurrency")
    processor = PerUserUpdateProcessor()
    rec = Recorder()
    flood = [
        submit(processor, message_update(FLOODER), rec.run(f"f{n}", hold=(n == 0)))
        for n in range(30)
    ]
    await spin(30)
    assert processor.pending_for(FLOODER) == 8  # over the cap: ordinary updates are dropped

    pre = submit(processor, pre_checkout_update(FLOODER), rec.run("pre-checkout"))
    paid = submit(processor, payment_update(FLOODER), rec.run("payment"))
    await asyncio.wait_for(pre, timeout=1)  # answered at once, while f0 still blocks the lock
    assert "end:pre-checkout" in rec.log and "start:payment" not in rec.log

    rec.gate("f0").set()
    await asyncio.wait_for(asyncio.gather(paid, *flood), timeout=2)
    assert "end:payment" in rec.log  # never dropped: processed after the earlier updates
    assert processor._drops[FLOODER].dropped + 1 == 22  # 30 ordinary - 8 admitted (1 reported)


# --- c: pre_checkout bypasses the user's own lock ----------------------------------------------


async def test_a_pre_checkout_is_answered_while_the_same_users_earlier_update_is_blocked(db):
    add_customer(A)
    order_id = place_order(A, {TEXT: 1, KEY: 2})
    context = stars_context()
    processor = PerUserUpdateProcessor()
    rec = Recorder()
    blocked = submit(processor, message_update(A), rec.run("slow-owner-action", hold=True))
    await spin()

    query = pre_checkout_query(A, str(order_id), orders_repo.get(order_id)["total_stars"])
    update = make_update(A, pre_checkout_query=query)
    await asyncio.wait_for(
        processor.process_update(update, payments.pre_checkout(update, context)), timeout=1
    )

    query.answer.assert_awaited_once_with(ok=True)  # answered; A's other update is still blocked
    assert not blocked.done()
    rec.gate("slow-owner-action").set()
    await blocked


# --- 5: double taps stay safe -------------------------------------------------------------------


async def test_a_double_tapped_pay_creates_one_order_and_reserves_keys_once(db):
    fill_cart(A, {TEXT: 1, KEY: 2})
    context = stars_context("mock")
    gate = asyncio.Event()

    async def slow_send(*args, **kwargs):
        await gate.wait()  # the first Pay is slow to present its payment screen

    context.bot.send_message.side_effect = slow_send
    processor = PerUserUpdateProcessor()

    def tap():
        update = make_update(A, callback_query=make_callback_query("co:pay"), chat_id=A)
        return submit(processor, update, checkout.checkout_pay(update, context))

    first = tap()
    await spin()
    second = tap()
    await spin(10)
    gate.set()
    await asyncio.wait_for(asyncio.gather(first, second), timeout=3)

    rows = db.execute("SELECT id FROM orders WHERE user_id = ?", (A,)).fetchall()
    assert len(rows) == 1
    assert key_states(db) == {"available": 8, "reserved": 2}  # reserved once, not twice
    assert processor.tracked_users == 0


async def test_a_double_tapped_refund_refunds_once(db):
    add_customer(A)
    order_id = place_order(A, {KEY: 2})
    from .shop import pay

    pay(order_id)
    context = stars_context()
    gate = asyncio.Event()

    async def slow_refund(**kwargs):
        await gate.wait()
        return True

    context.bot.refund_star_payment.side_effect = slow_refund
    processor = PerUserUpdateProcessor()

    def tap():
        update = make_update(
            OWNER_ID,
            callback_query=make_callback_query(f"adm:ord:refund:{order_id}:yes"),
            chat_id=OWNER_ID,
        )
        return submit(processor, update, admin_catalogue.route(update, context))

    first = tap()
    await spin()
    second = tap()
    await spin(10)
    gate.set()
    await asyncio.wait_for(asyncio.gather(first, second), timeout=3)

    assert context.bot.refund_star_payment.await_count == 1  # the provider was called once
    assert orders_repo.get(order_id)["status"] == "refunded"
    assert key_states(db) == {"available": 8, "revoked": 2}
    assert processor.tracked_users == 0


# --- A2: drops are logged in aggregate -----------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def _drop_many(processor, user_id: int, count: int) -> None:
    for n in range(count):
        await processor.process_update(message_update(user_id), Recorder().run(f"d{n}"))


def _reports(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if "Dropped" in r.getMessage()]


async def test_fifty_drops_in_one_window_give_one_line_then_the_next_window_reports_the_rest(
    caplog,
):
    caplog.set_level(logging.INFO, logger="bot.concurrency")
    clock = Clock()
    processor = PerUserUpdateProcessor(max_per_user=1, clock=clock)
    rec = Recorder()
    holder = submit(processor, message_update(A), rec.run("holder", hold=True))
    await spin()

    await _drop_many(processor, A, 50)  # all within the same instant
    assert _reports(caplog) == [
        f"Dropped 1 update(s) from user {A} (over the per-user limit) since the last report"
    ]  # the first drop is reported at once; the other 49 are only counted

    clock.now += 30  # still inside the window: nothing new
    await _drop_many(processor, A, 5)
    assert len(_reports(caplog)) == 1

    clock.now += 31  # the window has passed: one more line with everything since the last
    await _drop_many(processor, A, 1)
    assert _reports(caplog)[1] == (
        f"Dropped 55 update(s) from user {A} (over the per-user limit) since the last report"
    )  # 49 + 5 + 1
    assert len(_reports(caplog)) == 2

    rec.gate("holder").set()
    await holder


async def test_users_are_reported_independently(caplog):
    caplog.set_level(logging.INFO, logger="bot.concurrency")
    clock = Clock()
    processor = PerUserUpdateProcessor(max_per_user=1, clock=clock)
    rec = Recorder()
    first = submit(processor, message_update(A), rec.run("a", hold=True))
    second = submit(processor, message_update(B), rec.run("b", hold=True))
    await spin()
    await _drop_many(processor, A, 3)
    await _drop_many(processor, B, 3)
    lines = _reports(caplog)
    assert len(lines) == 2 and f"user {A}" in lines[0] and f"user {B}" in lines[1]
    rec.gate("a").set()
    rec.gate("b").set()
    await asyncio.gather(first, second)


async def test_drops_counted_when_a_flood_stops_are_reported_later_not_lost(caplog):
    caplog.set_level(logging.INFO, logger="bot.concurrency")
    clock = Clock()
    processor = PerUserUpdateProcessor(max_per_user=1, clock=clock)
    rec = Recorder()
    holder_a = submit(processor, message_update(A), rec.run("a", hold=True))
    holder_b = submit(processor, message_update(B), rec.run("b", hold=True))
    await spin()
    await _drop_many(processor, A, 10)  # 1 reported, 9 pending, then A goes quiet
    clock.now += 61
    await _drop_many(processor, B, 1)  # a later drop by someone else flushes A's remainder
    assert any(f"Dropped 9 update(s) from user {A}" in line for line in _reports(caplog))
    rec.gate("a").set()
    rec.gate("b").set()
    await asyncio.gather(holder_a, holder_b)


async def test_the_drop_map_is_pruned_and_shutdown_reports_the_remainder(caplog):
    caplog.set_level(logging.INFO, logger="bot.concurrency")
    clock = Clock()
    processor = PerUserUpdateProcessor(max_per_user=1, clock=clock)
    rec = Recorder()
    holder = submit(processor, message_update(A), rec.run("a", hold=True))
    await spin()
    await _drop_many(processor, A, 4)
    await processor.shutdown()
    assert f"Dropped 3 update(s) from user {A}" in "\n".join(_reports(caplog))
    assert processor._drops == {}

    clock.now += 500
    await _drop_many(processor, A, 1)  # rebuilt after a shutdown flush
    clock.now += 500
    await _drop_many(processor, B, 1)  # B's drop prunes A's long-quiet entry
    assert A not in processor._drops or processor._drops[A].dropped == 0
    rec.gate("a").set()
    await holder


async def test_the_drop_report_never_contains_update_content(caplog):
    caplog.set_level(logging.DEBUG, logger="bot.concurrency")
    processor = PerUserUpdateProcessor(max_per_user=1, clock=Clock())
    rec = Recorder()
    holder = submit(processor, message_update(A), rec.run("a", hold=True))
    await spin()
    update = message_update(A)
    update.message = SimpleNamespace(text="SECRET-TEXT", successful_payment=None)
    await processor.process_update(update, rec.run("x"))
    assert "SECRET-TEXT" not in caplog.text
    rec.gate("a").set()
    await holder
