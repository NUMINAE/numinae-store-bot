"""bot/tg.py: typed update access and the quiet-ignore error path."""

from __future__ import annotations

import logging

import pytest

from bot import main
from bot.tg import (
    IgnoreUpdate,
    require_data,
    require_message,
    require_query,
    require_text,
    require_user,
)

from .helpers import make_callback_query, make_context, make_message, make_update


def test_require_helpers_return_the_object_when_present():
    query = make_callback_query("x:y")
    message = make_message("hi")
    update = make_update(1001, callback_query=query, message=message)
    assert require_user(update).id == 1001
    assert require_query(update) is query
    assert require_message(update) is message
    assert require_data(query) == "x:y"
    assert require_text(message) == "hi"


def test_each_helper_raises_ignore_update_when_missing():
    update = make_update(None)
    for helper in (require_user, require_query, require_message):
        with pytest.raises(IgnoreUpdate):
            helper(update)
    no_data = make_callback_query()
    no_data.data = None
    with pytest.raises(IgnoreUpdate):
        require_data(no_data)
    with pytest.raises(IgnoreUpdate):
        require_text(make_message(None))


async def test_error_handler_treats_ignore_update_as_a_quiet_noop(caplog):
    caplog.set_level(logging.DEBUG)
    context = make_context()
    context.error = IgnoreUpdate("update has no effective user")
    await main.on_error(make_update(None), context)
    context.bot.send_message.assert_not_called()
    assert [r.levelno for r in caplog.records if r.name == "bot.main"] == [logging.DEBUG]


async def test_error_handler_still_logs_real_errors(caplog):
    caplog.set_level(logging.ERROR)
    context = make_context()
    context.error = RuntimeError("boom")
    await main.on_error(make_update(1001), context)
    assert any(r.levelno == logging.ERROR for r in caplog.records)


async def test_a_handler_given_an_update_without_a_user_raises_ignore_update(db):
    from bot.handlers import common

    update = make_update(None, message=make_message("/start"))
    with pytest.raises(IgnoreUpdate):
        await common.start(update, make_context())
