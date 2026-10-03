from __future__ import annotations

import logging

import pytest

from bot.repo import orders as orders_repo
from bot.security import get_owned_order, owner_only

from .conftest import OWNER_ID
from .helpers import (
    add_customer,
    make_callback_query,
    make_context,
    make_message,
    make_update,
)

OWNER_OF_ORDER = 1001
STRANGER = 1002
UNAVAILABLE = "That order is no longer available."


@pytest.fixture
def order_id(db):
    add_customer(OWNER_OF_ORDER)
    add_customer(STRANGER)
    cur = db.execute(
        "INSERT INTO orders (user_id, total_stars, item_count, payment_mode, status)"
        " VALUES (?, 75, 1, 'mock', 'pending_payment')",
        (OWNER_OF_ORDER,),
    )
    db.commit()
    return cur.lastrowid


# --- get_owned_order --------------------------------------------------------


async def test_owner_gets_the_row(order_id):
    update = make_update(OWNER_OF_ORDER, callback_query=make_callback_query())
    row = await get_owned_order(update, order_id)
    assert row is not None
    assert row["id"] == order_id
    update.callback_query.answer.assert_not_called()


async def test_non_owner_and_nonexistent_id_get_the_identical_response(order_id, caplog):
    caplog.set_level(logging.WARNING)

    stranger = make_update(STRANGER, callback_query=make_callback_query())
    assert await get_owned_order(stranger, order_id) is None

    probe = make_update(STRANGER, callback_query=make_callback_query())
    assert await get_owned_order(probe, 99999) is None

    stranger.callback_query.answer.assert_awaited_once_with(UNAVAILABLE, show_alert=True)
    assert probe.callback_query.answer.await_args == stranger.callback_query.answer.await_args

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2
    assert f"order_id={order_id}" in warnings[0].getMessage()
    assert "order_id=99999" in warnings[1].getMessage()


async def test_message_updates_get_an_identical_reply(order_id, caplog):
    caplog.set_level(logging.WARNING)
    stranger = make_update(STRANGER, message=make_message("hi"))
    probe = make_update(STRANGER, message=make_message("hi"))
    assert await get_owned_order(stranger, order_id) is None
    assert await get_owned_order(probe, 424242) is None
    stranger.message.reply_text.assert_awaited_once_with(UNAVAILABLE)
    probe.message.reply_text.assert_awaited_once_with(UNAVAILABLE)


async def test_missing_user_is_refused(order_id, caplog):
    caplog.set_level(logging.WARNING)
    update = make_update(None, callback_query=make_callback_query())
    assert await get_owned_order(update, order_id) is None
    update.callback_query.answer.assert_awaited_once_with(UNAVAILABLE, show_alert=True)
    assert any(r.levelno == logging.WARNING for r in caplog.records)


async def test_owner_of_the_shop_is_not_automatically_the_order_owner(order_id):
    update = make_update(OWNER_ID, callback_query=make_callback_query())
    assert await get_owned_order(update, order_id) is None


async def test_orders_repo_is_what_get_owned_order_consults(order_id):
    assert orders_repo.get(order_id)["user_id"] == OWNER_OF_ORDER


# --- owner_only -------------------------------------------------------------


async def test_owner_only_lets_the_owner_through():
    calls = []

    @owner_only
    async def handler(update, context):
        calls.append(update)

    update = make_update(OWNER_ID, message=make_message("/x"))
    await handler(update, make_context())
    assert calls == [update]
    update.message.reply_text.assert_not_called()


async def test_owner_only_refuses_others_on_messages(caplog):
    caplog.set_level(logging.WARNING)
    calls = []

    @owner_only
    async def handler(update, context):
        calls.append(update)

    update = make_update(STRANGER, message=make_message("/x"))
    await handler(update, make_context())
    assert calls == []
    update.message.reply_text.assert_awaited_once_with("You are not authorized to do that.")
    assert any("Refused owner-only action" in r.getMessage() for r in caplog.records)


async def test_owner_only_refuses_others_on_callbacks():
    @owner_only
    async def handler(update, context):
        raise AssertionError("must not run")

    update = make_update(STRANGER, callback_query=make_callback_query())
    await handler(update, make_context())
    update.callback_query.answer.assert_awaited_once_with(
        "You are not authorized to do that.", show_alert=True
    )


async def test_owner_only_refuses_when_there_is_no_user():
    @owner_only
    async def handler(update, context):
        raise AssertionError("must not run")

    update = make_update(None, message=make_message("/x"))
    await handler(update, make_context())
    update.message.reply_text.assert_awaited_once()


def test_owner_only_preserves_the_wrapped_name():
    @owner_only
    async def my_handler(update, context):
        return None

    assert my_handler.__name__ == "my_handler"
