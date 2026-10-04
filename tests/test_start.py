"""/start: straight to the main menu and the reply keyboard, for every user."""

from __future__ import annotations

import pytest

from bot.handlers import common
from bot.keyboards import MENU_LABELS
from bot.repo import customers as customers_repo
from bot.repo import settings as settings_repo

from .conftest import OWNER_ID
from .helpers import add_customer, make_context, make_message, make_update

NEWCOMER = 4242


async def start(user_id: int):
    message = make_message("/start")
    context = make_context()
    await common.start(make_update(user_id, message=message, chat_id=user_id), context)
    return message, context


def inline_labels(markup) -> list[str]:
    return [b.text for row in markup.inline_keyboard for b in row]


async def test_a_brand_new_user_gets_the_main_menu_and_the_reply_keyboard_with_no_picker(db):
    assert customers_repo.get(NEWCOMER) is None
    message, _ = await start(NEWCOMER)

    menu_call, keyboard_call = message.reply_text.await_args_list
    text = menu_call.args[0]
    assert "Welcome to Numinae Store" in text
    assert "choose your language" not in text.lower()
    labels = inline_labels(menu_call.kwargs["reply_markup"])
    assert labels == ["Browse products", "My cart", "My Library", "Help"]
    reply_keyboard = keyboard_call.kwargs["reply_markup"]
    assert [b.text for b in reply_keyboard.keyboard[0]] == list(MENU_LABELS)
    assert reply_keyboard.is_persistent
    assert customers_repo.get(NEWCOMER) is not None  # and they are recorded


async def test_a_returning_user_gets_exactly_the_same_screens(db):
    add_customer(NEWCOMER)
    new_message, _ = await start(7001)
    old_message, _ = await start(NEWCOMER)
    for message in (new_message, old_message):
        assert len(message.reply_text.await_args_list) == 2
    assert [c.args[0] for c in new_message.reply_text.await_args_list] == [
        c.args[0] for c in old_message.reply_text.await_args_list
    ]


@pytest.mark.parametrize("known", [False, True])
async def test_the_welcome_text_setting_is_shown_escaped(db, known):
    if known:
        add_customer(NEWCOMER)
    settings_repo.set("welcome_text", "Hello <b>&")
    message, _ = await start(NEWCOMER)
    assert message.reply_text.await_args_list[0].args[0].endswith("\n\nHello &lt;b&gt;&amp;")


async def test_a_new_customer_is_reported_to_the_owner_after_their_own_replies(db):
    message, context = await start(NEWCOMER)
    assert len(message.reply_text.await_args_list) == 2  # the customer was served first
    (call,) = context.bot.send_message.await_args_list
    assert call.args[0] == OWNER_ID and "New customer" in call.args[1]


async def test_a_returning_customer_is_not_reported_to_the_owner_again(db):
    add_customer(NEWCOMER)
    _, context = await start(NEWCOMER)
    context.bot.send_message.assert_not_called()


async def test_the_owner_notification_can_be_switched_off(db):
    settings_repo.set("notify_new_customers", "0")
    message, context = await start(NEWCOMER)
    assert len(message.reply_text.await_args_list) == 2
    context.bot.send_message.assert_not_called()
