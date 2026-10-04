"""handlers/common.py: /start's owner notification, the Home screen, customer
bookkeeping that must never take a screen down, the help screen and the catch-all."""

from __future__ import annotations

import logging
from types import SimpleNamespace

from bot.handlers import common, library
from bot.repo import admin_pending as pending_repo
from bot.repo import cart as cart_repo
from bot.repo import customers as customers_repo
from bot.repo import settings as settings_repo
from bot.ui_kit import ICON

from .conftest import OWNER_ID
from .flow import Flow
from .helpers import (
    HOME,
    add_customer,
    make_callback_query,
    make_context,
    make_message,
    make_update,
)
from .shop import TEXT, pay, place_order

NEWCOMER = 6001


async def start(user_id: int, **user_fields):
    message = make_message("/start")
    context = make_context()
    update = make_update(user_id, message=message, chat_id=user_id)
    for name, value in user_fields.items():
        setattr(update.effective_user, name, value)
    await common.start(update, context)
    return message, context


def owner_messages(context) -> list[str]:
    return [
        str(c.args[1] if len(c.args) > 1 else c.kwargs["text"])
        for c in context.bot.send_message.await_args_list
        if (c.args[0] if c.args else c.kwargs.get("chat_id")) == OWNER_ID
    ]


# --- /start tells the owner about a new customer ------------------------------------------


async def test_a_new_customer_is_reported_to_the_owner_after_their_own_replies(db):
    message, context = await start(NEWCOMER, username="newbie", first_name="Nia")

    assert message.reply_text.await_count == 2  # the customer is served first
    (notice,) = owner_messages(context)
    assert "New customer" in notice and "Nia" in notice and "@newbie" in notice
    assert str(NEWCOMER) in notice
    assert notice.startswith(f"{ICON.info} <b>New customer</b>")


async def test_a_returning_customer_is_not_reported(db):
    add_customer(NEWCOMER)
    _, context = await start(NEWCOMER)
    assert owner_messages(context) == []


async def test_the_owner_can_switch_new_customer_notices_off(db):
    settings_repo.set("notify_new_customers", "0")
    _, context = await start(NEWCOMER)
    assert owner_messages(context) == []


async def test_a_name_with_markup_in_the_notice_is_escaped_once(db):
    _, context = await start(NEWCOMER, first_name="<b>&", username=None)
    (notice,) = owner_messages(context)
    assert "&lt;b&gt;&amp;" in notice and "&amp;amp;" not in notice and "<b>&" not in notice


async def test_a_failing_owner_notice_does_not_break_start(db, caplog):
    caplog.set_level(logging.ERROR)
    message = make_message("/start")
    context = make_context()

    async def send(chat_id, text=None, **kwargs):
        raise RuntimeError("owner blocked the bot")

    context.bot.send_message.side_effect = send
    await common.start(make_update(NEWCOMER, message=message, chat_id=NEWCOMER), context)
    assert message.reply_text.await_count == 2
    assert any("new-customer notification" in r.getMessage() for r in caplog.records)


# --- bookkeeping never takes a screen down -----------------------------------------------------


def test_touch_customer_reports_new_then_known(db):
    update = make_update(NEWCOMER)
    assert common.touch_customer(update) is True
    assert common.touch_customer(update) is False


def test_touch_customer_without_a_user_does_nothing(db):
    assert common.touch_customer(make_update(None)) is False


def test_touch_customer_swallows_a_database_failure(db, monkeypatch, caplog):
    caplog.set_level(logging.ERROR)

    def boom(*args, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(customers_repo, "upsert", boom)
    assert common.touch_customer(make_update(NEWCOMER)) is False
    assert any(r.levelno == logging.ERROR and "customer" in r.getMessage() for r in caplog.records)


async def test_a_bookkeeping_failure_does_not_block_start_or_home(db, monkeypatch):
    monkeypatch.setattr(customers_repo, "upsert", lambda *a, **k: 1 / 0)
    message, context = await start(NEWCOMER)
    assert message.reply_text.await_count == 2  # the menu and the reply keyboard
    assert owner_messages(context) == []  # a failed upsert is "not a new customer"


async def test_home_still_renders_when_bookkeeping_fails(db, monkeypatch):
    monkeypatch.setattr(customers_repo, "upsert", lambda *a, **k: 1 / 0)
    flow = Flow(monkeypatch, NEWCOMER)
    await flow.tap(common.menu_show, "menu:show")
    assert flow.text.startswith(f"{ICON.home} <b>Numinae Store</b>")


# --- the Home screen ---------------------------------------------------------------------------


async def test_home_shows_the_default_welcome_and_the_four_menu_buttons(db, monkeypatch):
    flow = Flow(monkeypatch, NEWCOMER)
    await flow.tap(common.menu_show, "menu:show")
    assert flow.text.startswith(f"{ICON.home} <b>Numinae Store</b>\n\n")
    assert "Welcome to Numinae Store" in flow.text
    assert flow.labels() == ["Browse products", "My cart", "My Library", "Help"]
    assert HOME not in flow.labels()  # Home is the one screen without a nav row


async def test_home_shows_the_cart_count_on_the_cart_button(db, monkeypatch):
    add_customer(NEWCOMER)
    cart_repo.add(NEWCOMER, TEXT, 1)
    flow = Flow(monkeypatch, NEWCOMER)
    await flow.tap(common.menu_show, "menu:show")
    assert "My cart (1)" in flow.labels()


async def test_home_shows_the_owners_welcome_text_escaped_once_and_trimmed(db, monkeypatch):
    settings_repo.set("welcome_text", "Hi <b>& " + "x" * 2000)
    flow = Flow(monkeypatch, NEWCOMER)
    await flow.tap(common.menu_show, "menu:show")
    assert "Hi &lt;b&gt;&amp; " in flow.text and "&amp;amp;" not in flow.text
    assert len(flow.text) < common.WELCOME_BUDGET + 100
    assert flow.text.rstrip().endswith("…")


async def test_help_has_a_header_and_the_nav_row(db, monkeypatch):
    flow = Flow(monkeypatch, NEWCOMER)
    await flow.tap(common.help_placeholder, "help:show")
    assert flow.text.startswith(f"{ICON.info} <b>")
    assert flow.labels() == [HOME]


async def test_going_home_abandons_a_pending_admin_prompt(db, monkeypatch):
    pending_repo.set(OWNER_ID, "cat_create_name")
    flow = Flow(monkeypatch, OWNER_ID)
    await flow.tap(common.menu_show, "menu:show")
    assert pending_repo.get(OWNER_ID) is None  # the next typed message is not swallowed


# --- the catch-all -----------------------------------------------------------------------------


async def test_noop_is_acknowledged_silently(db):
    context = make_context()
    query = make_callback_query("noop")
    await common.unknown_callback(make_update(NEWCOMER, callback_query=query), context)
    query.answer.assert_awaited_once_with()
    context.bot.send_message.assert_not_called()


async def test_an_unknown_callback_alerts_and_offers_the_home_screen(db):
    context = make_context()
    message = SimpleNamespace(chat=SimpleNamespace(id=NEWCOMER))
    query = make_callback_query("zzz:1", message=message)
    await common.unknown_callback(make_update(NEWCOMER, callback_query=query), context)

    assert query.answer.await_args.kwargs["show_alert"] is True
    kwargs = context.bot.send_message.await_args.kwargs
    assert kwargs["chat_id"] == NEWCOMER
    assert kwargs["text"].startswith(f"{ICON.home} <b>Numinae Store</b>")
    assert kwargs["reply_markup"].inline_keyboard  # the menu


async def test_an_unknown_callback_without_a_user_still_answers(db):
    context = make_context()
    message = SimpleNamespace(chat=SimpleNamespace(id=NEWCOMER))
    query = make_callback_query("zzz:1", message=message)
    update = make_update(None, callback_query=query)
    await common.unknown_callback(update, context)
    kwargs = context.bot.send_message.await_args.kwargs
    assert kwargs["text"].startswith(f"{ICON.home} <b>Numinae Store</b>")
    assert kwargs["reply_markup"].inline_keyboard == ()


async def test_an_unknown_callback_on_an_inaccessible_message_only_alerts(db):
    context = make_context()
    query = make_callback_query("zzz:1")
    query.message = None
    await common.unknown_callback(make_update(NEWCOMER, callback_query=query), context)
    query.answer.assert_awaited_once()
    context.bot.send_message.assert_not_called()


# --- /library ----------------------------------------------------------------------------------


async def test_the_library_command_opens_the_library_screen(db):
    add_customer(NEWCOMER)
    order_id = place_order(NEWCOMER, {TEXT: 1}, mode="mock")
    pay(order_id)
    message = make_message("/library")
    context = make_context()
    await library.library_command(make_update(NEWCOMER, message=message, chat_id=NEWCOMER), context)

    sent = context.bot.send_message.await_args.kwargs
    assert sent["text"].startswith(f"{ICON.library} <b>")
    assert sent["reply_markup"].inline_keyboard[-1][-1].callback_data == "menu:show"
