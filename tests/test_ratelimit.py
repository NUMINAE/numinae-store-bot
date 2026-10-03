from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from telegram.ext import ApplicationHandlerStop

from bot import ratelimit

from .conftest import OWNER_ID
from .helpers import make_callback_query, make_context, make_message, make_update

USER = 2001


@pytest.fixture
def clock(monkeypatch, clean_ratelimit):
    """Controllable monotonic clock. Only the ratelimit module's `time` is
    replaced, so the asyncio event loop keeps the real one."""
    now = [1000.0]
    monkeypatch.setattr(ratelimit, "time", SimpleNamespace(monotonic=lambda: now[0]))
    return now


async def _hit(user_id: int = USER, *, callback: bool = True):
    if callback:
        update = make_update(user_id, callback_query=make_callback_query())
    else:
        update = make_update(user_id, message=make_message("hi"))
    await ratelimit.throttle(update, make_context())
    return update


async def test_first_twenty_interactions_pass_and_the_21st_is_blocked(clock):
    for _ in range(ratelimit.LIMIT):
        update = await _hit()
        update.callback_query.answer.assert_not_called()
    with pytest.raises(ApplicationHandlerStop):
        await _hit()


async def test_blocked_callback_gets_an_alert(clock):
    for _ in range(ratelimit.LIMIT):
        await _hit()
    update = make_update(USER, callback_query=make_callback_query())
    with pytest.raises(ApplicationHandlerStop):
        await ratelimit.throttle(update, make_context())
    update.callback_query.answer.assert_awaited_once()
    assert update.callback_query.answer.await_args.kwargs == {"show_alert": True}


async def test_blocked_message_gets_a_reply(clock):
    for _ in range(ratelimit.LIMIT):
        await _hit(callback=False)
    update = make_update(USER, message=make_message("hi"))
    with pytest.raises(ApplicationHandlerStop):
        await ratelimit.throttle(update, make_context())
    update.message.reply_text.assert_awaited_once()


async def test_blocked_update_without_message_or_callback_is_still_stopped(clock):
    for _ in range(ratelimit.LIMIT):
        await _hit()
    with pytest.raises(ApplicationHandlerStop):
        await ratelimit.throttle(make_update(USER), make_context())


async def test_interactions_pass_again_once_the_window_slides(clock):
    for _ in range(ratelimit.LIMIT):
        await _hit()
    with pytest.raises(ApplicationHandlerStop):
        await _hit()
    clock[0] += ratelimit.WINDOW_SECONDS + 0.1
    await _hit()  # no exception


async def test_users_are_throttled_independently(clock):
    for _ in range(ratelimit.LIMIT):
        await _hit(USER)
    with pytest.raises(ApplicationHandlerStop):
        await _hit(USER)
    await _hit(USER + 1)  # unaffected


async def test_owner_is_exempt(clock):
    for _ in range(ratelimit.LIMIT * 3):
        await _hit(OWNER_ID)
    assert OWNER_ID not in ratelimit._hits


async def test_update_without_a_user_is_ignored(clock):
    await ratelimit.throttle(make_update(None, message=make_message("x")), make_context())
    assert ratelimit._hits == {}


async def test_warning_is_logged_once_per_window(clock, caplog):
    caplog.set_level(logging.WARNING)
    for _ in range(ratelimit.LIMIT):
        await _hit()
    for _ in range(3):
        with pytest.raises(ApplicationHandlerStop):
            await _hit()
    tripped = [r for r in caplog.records if "Rate limit tripped" in r.getMessage()]
    assert len(tripped) == 1


async def test_idle_entries_are_pruned_by_the_periodic_gc(clock):
    await _hit(USER)
    await _hit(USER + 1)
    assert set(ratelimit._hits) == {USER, USER + 1}
    ratelimit._warned_until[USER] = 5.0

    clock[0] += ratelimit.WINDOW_SECONDS + 1
    ratelimit._calls_since_gc = ratelimit._GC_INTERVAL - 1  # next call triggers the sweep
    await _hit(USER + 2)

    assert set(ratelimit._hits) == {USER + 2}
    assert USER not in ratelimit._warned_until


def test_gc_keeps_users_with_recent_hits(clock):
    from collections import deque

    ratelimit._hits[1] = deque([clock[0] - 100.0, clock[0] - 1.0])
    ratelimit._hits[2] = deque([clock[0] - 100.0])
    ratelimit._gc(clock[0])
    assert set(ratelimit._hits) == {1}
    assert list(ratelimit._hits[1]) == [clock[0] - 1.0]
