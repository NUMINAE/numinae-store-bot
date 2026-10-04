"""Stars and mock providers with a fake bot."""

from __future__ import annotations

import inspect
import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from telegram import Bot
from telegram.constants import InvoiceLimit
from telegram.error import BadRequest

from bot.payments.mock import MockProvider
from bot.payments.registry import build_provider
from bot.payments.stars import StarsProvider, invoice_payload, truncate
from bot.repo import orders as orders_repo

from .shop import KEY, TEXT, place_order

USER = 3001


def fake_bot() -> Any:
    return SimpleNamespace(
        send_invoice=AsyncMock(), send_message=AsyncMock(), refund_star_payment=AsyncMock()
    )


def order_and_items(lines=None, mode="stars") -> tuple[Any, Any]:
    order_id = place_order(USER, lines or {TEXT: 1, KEY: 2}, mode=mode)
    return orders_repo.get_with_items(order_id)


# --- Stars ------------------------------------------------------------------------------------


async def test_stars_invoice_arguments(db):
    order, items = order_and_items()
    bot = fake_bot()
    await StarsProvider().present_checkout(bot, USER, order, items)

    (call,) = bot.send_invoice.await_args_list
    kwargs = call.kwargs
    assert kwargs["chat_id"] == USER
    assert kwargs["currency"] == "XTR"
    assert "provider_token" not in kwargs  # Stars invoices carry no provider token
    assert kwargs["payload"] == str(order["id"])  # the order id and nothing else
    (price,) = kwargs["prices"]  # exactly one price line
    assert price.amount == order["total_stars"] == 75 + 100
    assert kwargs["title"] == f"Order #{order['id']}"
    assert "Neon Portrait Prompt Pack — Personal" in kwargs["description"]
    assert "Orbit Icons Pro — Personal × 2" in kwargs["description"]


async def test_the_pay_button_is_first_and_cancel_is_on_a_later_row(db):
    order, items = order_and_items()
    bot = fake_bot()
    await StarsProvider().present_checkout(bot, USER, order, items)
    rows = bot.send_invoice.await_args_list[0].kwargs["reply_markup"].inline_keyboard
    assert rows[0][0].pay is True and "175" in rows[0][0].text
    assert len(rows[0]) == 1
    assert rows[1][0].callback_data == f"co:cancel:{order['id']}"
    assert rows[1][0].pay is not True


async def test_the_invoice_arguments_bind_to_the_installed_bot_signature(db):
    order, items = order_and_items()
    bot = fake_bot()
    await StarsProvider().present_checkout(bot, USER, order, items)
    kwargs = bot.send_invoice.await_args_list[0].kwargs
    inspect.signature(Bot.send_invoice).bind(object(), **kwargs)


async def test_long_titles_and_descriptions_are_truncated_to_telegrams_limits(db):
    order, items = order_and_items({TEXT: 1})
    long_items = [
        {**dict(items[0]), "product_name": "P" * 300, "variant_label": "V" * 300, "quantity": 1}
    ] * 3
    big_order = {**dict(order), "id": 10**9}
    bot = fake_bot()
    await StarsProvider().present_checkout(bot, USER, big_order, long_items)
    kwargs = bot.send_invoice.await_args_list[0].kwargs
    assert len(kwargs["title"]) <= InvoiceLimit.MAX_TITLE_LENGTH
    assert len(kwargs["description"]) <= InvoiceLimit.MAX_DESCRIPTION_LENGTH
    assert kwargs["description"].endswith("…")
    assert len(kwargs["payload"].encode()) <= InvoiceLimit.MAX_PAYLOAD_LENGTH


def test_truncate_is_plain_text_safe():
    assert truncate("short", 10) == "short"
    assert truncate("x" * 40, 32) == "x" * 31 + "…"
    assert len(truncate("é" * 400, 255)) == 255
    # No markup handling at all: tags are just characters, never "closed" or repaired.
    cut = truncate("<b>" + "a" * 50 + "</b>", 20)
    assert len(cut) == 20 and cut.endswith("…")


def test_invoice_payload_is_just_the_id():
    assert invoice_payload(42) == "42"


async def test_invoice_text_is_plain_not_html_escaped(db):
    order, items = order_and_items({TEXT: 1})
    hostile = [{**dict(items[0]), "product_name": "<b>&"}]
    bot = fake_bot()
    await StarsProvider().present_checkout(bot, USER, order, hostile)
    kwargs = bot.send_invoice.await_args_list[0].kwargs
    assert "<b>&" in kwargs["description"]
    assert "parse_mode" not in kwargs


async def test_stars_refund_calls_the_api_with_user_and_charge_id():
    bot = fake_bot()
    bot.refund_star_payment.return_value = True
    assert await StarsProvider().refund(bot, USER, "ch_9") is True
    bot.refund_star_payment.assert_awaited_once_with(
        user_id=USER, telegram_payment_charge_id="ch_9"
    )


async def test_stars_refund_failure_returns_false_and_logs(caplog):
    caplog.set_level(logging.ERROR)
    bot = fake_bot()
    bot.refund_star_payment.side_effect = BadRequest("CHARGE_ALREADY_REFUNDED")
    assert await StarsProvider().refund(bot, USER, "ch_9") is False
    assert any("ch_9" in r.getMessage() for r in caplog.records)


# --- mock -------------------------------------------------------------------------------------


async def test_mock_checkout_shows_a_demo_banner_and_the_demo_buttons(db):
    order, items = order_and_items(mode="mock")
    bot = fake_bot()
    await MockProvider().present_checkout(bot, USER, order, items)

    (call,) = bot.send_message.await_args_list
    assert "DEMO MODE" in call.kwargs["text"] and "no real payment" in call.kwargs["text"]
    assert "⭐ 175" in call.kwargs["text"]
    assert "Orbit Icons Pro — Personal × 2" in call.kwargs["text"]
    markup = call.kwargs["reply_markup"]
    buttons = [(b.text, b.callback_data) for row in markup.inline_keyboard for b in row]
    assert buttons == [
        ("Pay (demo)", f"co:mockpay:{order['id']}"),
        ("Cancel order", f"co:cancel:{order['id']}"),
    ]
    bot.send_invoice.assert_not_called()


async def test_mock_checkout_escapes_hostile_names(db):
    order, items = order_and_items({TEXT: 1}, mode="mock")
    hostile = [{**dict(items[0]), "product_name": "<b>&"}]
    bot = fake_bot()
    await MockProvider().present_checkout(bot, USER, order, hostile)
    assert "&lt;b&gt;&amp;" in bot.send_message.await_args_list[0].kwargs["text"]


async def test_mock_refund_is_a_noop_that_reports_success():
    bot = fake_bot()
    assert await MockProvider().refund(bot, USER, "mock_x") is True
    bot.refund_star_payment.assert_not_called()


# --- registry ---------------------------------------------------------------------------------


def test_build_provider_picks_by_mode_without_network_calls():
    assert isinstance(build_provider("stars"), StarsProvider)
    assert isinstance(build_provider("mock"), MockProvider)
    assert build_provider("stars").mode == "stars"
    assert build_provider("mock").mode == "mock"
    with pytest.raises(ValueError, match="Unknown payment mode"):
        build_provider("other")
