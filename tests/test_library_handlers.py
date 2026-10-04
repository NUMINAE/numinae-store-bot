"""My Library screens through the real handlers."""

from __future__ import annotations

import pytest

from bot.handlers import library, router
from bot.keyboards import MENU_LABELS
from bot.repo import orders as orders_repo
from bot.ui_kit import ICON

from .flow import Flow
from .helpers import add_customer, make_context
from .shop import KEY, TEXT, pay, place_order

USER = 3001
OTHER = 3002


@pytest.fixture
def flow(db, monkeypatch):
    add_customer(USER)
    return Flow(monkeypatch, USER, mode="mock")


def buy(lines, user=USER, delivered=True) -> int:
    order_id = place_order(user, lines, mode="mock")
    pay(order_id)
    if delivered:
        for item in orders_repo.list_undelivered_items(order_id):
            orders_repo.mark_item_delivered(item["id"])
        orders_repo.finish_order_if_delivered(order_id)
    return order_id


def item_ids(order_id: int) -> list[int]:
    return [i["id"] for i in orders_repo.get_with_items(order_id)[1]]


async def test_an_empty_library_is_friendly_and_offers_browse(flow):
    await flow.tap(library.route, "lib:list:0")
    assert "library is empty" in flow.text
    assert "Browse products" in flow.labels() and "🏠 Home" in flow.labels()


async def test_items_are_listed_newest_first_with_their_status(flow, db):
    first = buy({TEXT: 1})
    second = buy({KEY: 1}, delivered=False)
    db.execute("UPDATE orders SET created_at = '2026-01-01 00:00:01' WHERE id = ?", (first,))
    db.execute("UPDATE orders SET created_at = '2026-01-01 00:00:02' WHERE id = ?", (second,))
    db.commit()

    await flow.tap(library.route, "lib:list:0")

    labels = flow.labels()
    assert labels[0] == f"{ICON.paid} Orbit Icons Pro — Personal · Being delivered"
    assert labels[1] == f"{ICON.delivered} Neon Portrait Prompt Pack — Personal · Delivered"


async def test_paging_is_five_per_page_with_arrows_omitted_at_the_edges(flow):
    buy({TEXT: 1, 2: 1, KEY: 1})
    buy({TEXT: 1, 2: 1, KEY: 1})
    buy({TEXT: 1})
    total = orders_repo.count_library_items(USER)
    assert total == 7  # 3 + 3 + 1

    await flow.tap(library.route, "lib:list:0")
    item_buttons = [d for _, d in flow.buttons if d.startswith("lib:item:")]
    assert len(item_buttons) == 5
    assert "◀" not in flow.labels() and "▶" in flow.labels()
    assert "1 / 2" in flow.labels()

    await flow.tap(library.route, "lib:list:1")
    assert len([d for _, d in flow.buttons if d.startswith("lib:item:")]) == 2
    assert "◀" in flow.labels() and "▶" not in flow.labels()

    await flow.tap(library.route, "lib:list:99")  # out of range clamps to the last page
    assert "2 / 2" in flow.labels()


async def test_a_single_page_has_no_arrows_or_page_label(flow):
    buy({TEXT: 1})
    await flow.tap(library.route, "lib:list:0")
    assert "◀" not in flow.labels() and "▶" not in flow.labels()
    assert not any(label.startswith("Page") for label in flow.labels())


async def test_a_delivered_item_screen_offers_send_again(flow):
    order_id = buy({KEY: 2})
    (item_id,) = item_ids(order_id)
    await flow.tap(library.route, f"lib:item:{item_id}")
    assert "Orbit Icons Pro — Personal" in flow.text
    assert f"Order #{order_id}" in flow.text and f"Status: {ICON.delivered} Delivered" in flow.text
    assert f"lib:send:{item_id}" in [d for _, d in flow.buttons]
    assert flow.data_for("‹ Back") == "lib:list:0"


async def test_a_paid_but_undelivered_item_says_being_delivered_and_cannot_be_sent(flow):
    order_id = buy({TEXT: 1}, delivered=False)
    (item_id,) = item_ids(order_id)
    await flow.tap(library.route, f"lib:item:{item_id}")
    assert f"{ICON.paid} Being delivered" in flow.text
    assert not any(d.startswith("lib:send:") for _, d in flow.buttons)

    await flow.tap(library.route, f"lib:send:{item_id}")  # a stale or crafted tap
    assert flow.alert == "That item is not available."
    flow.context.bot.send_message.assert_not_called()


async def test_refunded_items_say_refunded_and_offer_no_send(flow):
    order_id = buy({TEXT: 1})
    orders_repo.set_status(order_id, "refunded", "owner")
    (item_id,) = item_ids(order_id)

    await flow.tap(library.route, "lib:list:0")
    assert flow.labels()[0].endswith("· Refunded")
    await flow.tap(library.route, f"lib:item:{item_id}")
    assert f"Status: {ICON.refunded} Refunded" in flow.text
    assert not any(d.startswith("lib:send:") for _, d in flow.buttons)

    await flow.tap(library.route, f"lib:send:{item_id}")
    assert flow.alert == "That item is not available."
    flow.context.bot.send_message.assert_not_called()


async def test_send_again_resends_the_goods(flow):
    order_id = buy({TEXT: 1})
    (item_id,) = item_ids(order_id)
    await flow.tap(library.route, f"lib:send:{item_id}")
    (call,) = flow.sent_messages()
    assert call.kwargs["chat_id"] == USER and "example.com/demo" in call.kwargs["text"]
    flow.query.answer.assert_awaited_with("Sent again.")


async def test_a_failed_resend_says_so(flow):
    order_id = buy({TEXT: 1})
    (item_id,) = item_ids(order_id)
    flow.context.bot.send_message.side_effect = RuntimeError("down")
    await flow.tap(library.route, f"lib:send:{item_id}")
    assert flow.alert == "Couldn't send it right now. Try again in a moment."


async def test_someone_elses_item_and_a_missing_item_get_the_identical_response(flow, monkeypatch):
    add_customer(OTHER)
    foreign = item_ids(buy({TEXT: 1}, user=OTHER))[0]
    for action in ("item", "send"):
        await flow.tap(library.route, f"lib:{action}:{foreign}")
        theirs = (flow.alert, len(flow.screens), len(flow.sent_messages()))
        await flow.tap(library.route, f"lib:{action}:999999")
        missing = (flow.alert, len(flow.screens), len(flow.sent_messages()))
        assert theirs == missing
        assert flow.alert == "That order is no longer available."
    assert flow.screens == []  # nothing about the foreign item was ever rendered


async def test_the_reply_keyboard_button_opens_the_library(flow):
    await flow.send(router.route_text, MENU_LABELS[2])
    (sent,) = flow.sent_messages()
    assert "library is empty" in str(sent.kwargs["text"]).lower()


async def test_malformed_library_callbacks_alert(flow):
    for data in ("lib:list:x", "lib:item", "lib:bogus:1", "lib:list:1:2", "lib:item:-1"):
        await flow.tap(library.route, data)
        assert flow.alert == "This menu has expired."


def test_context_factory_has_what_the_handlers_need():
    context = make_context()
    assert context.bot_data == {} and context.application.create_task
