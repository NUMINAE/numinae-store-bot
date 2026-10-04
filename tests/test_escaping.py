"""A name full of markup characters must appear on every screen exactly once, escaped
exactly once: never raw (it would become a tag), never twice-escaped (it would show
"&amp;" to the customer)."""

from __future__ import annotations

import pytest

from bot import delivery
from bot.repo import cart as cart_repo
from bot.repo import orders as orders_repo
from bot.repo import products as products_repo
from bot.repo import variants as variants_repo

from .flow import Flow
from .helpers import add_customer
from .screens import markup_problems, tap
from .shop import TEXT, pay, place_order
from .test_delivery import fake_bot

NAME = "A & B <i>"
ESCAPED = "A &amp; B &lt;i&gt;"
USER = 5001
PRODUCT = 1  # the demo product that owns edition TEXT


@pytest.fixture
def hostile_name(db):
    products_repo.update_name(PRODUCT, NAME)
    add_customer(USER)


@pytest.fixture
def flow(hostile_name, monkeypatch):
    return Flow(monkeypatch, USER, mode="mock")


def assert_name_escaped_once(text: str) -> None:
    assert text.count(ESCAPED) == 1, text
    assert NAME not in text  # never raw
    assert markup_problems(text) == []  # includes: nothing double-escaped, tags balanced


async def test_the_product_card(flow):
    await tap(flow, f"cat:p:{PRODUCT}:0")
    assert_name_escaped_once(flow.text)


async def test_the_edition_screen(flow):
    await tap(flow, f"cat:v:{TEXT}:0")
    assert_name_escaped_once(flow.text)


async def test_the_cart(flow):
    cart_repo.add(USER, TEXT, 1)
    await tap(flow, "cart:view")
    assert_name_escaped_once(flow.text)


async def test_the_checkout_review(flow):
    cart_repo.add(USER, TEXT, 1)
    await tap(flow, "co:start")
    assert_name_escaped_once(flow.text)


async def test_a_library_item(flow):
    order_id = place_order(USER, {TEXT: 1}, mode="mock")
    pay(order_id)
    for item in orders_repo.list_undelivered_items(order_id):
        orders_repo.mark_item_delivered(item["id"])
    orders_repo.finish_order_if_delivered(order_id)
    (item,) = orders_repo.get_with_items(order_id)[1]
    await tap(flow, f"lib:item:{item['id']}")
    assert_name_escaped_once(flow.text)


async def test_a_delivery_message(hostile_name):
    order_id = place_order(USER, {TEXT: 1}, mode="mock")
    pay(order_id)
    bot = fake_bot()
    await delivery.deliver_order(bot, order_id)
    delivery_message, receipt = [str(c.kwargs["text"]) for c in bot.send_message.await_args_list]
    assert_name_escaped_once(delivery_message)
    assert_name_escaped_once(receipt)


async def test_a_file_delivery_caption(hostile_name, db):
    db.execute(
        "UPDATE product_variants SET delivery_kind = 'file', delivery_file_id = 'FILE123',"
        " delivery_text = NULL WHERE id = ?",
        (TEXT,),
    )
    db.commit()
    order_id = place_order(USER, {TEXT: 1}, mode="mock")
    pay(order_id)
    bot = fake_bot()
    await delivery.deliver_order(bot, order_id)
    assert_name_escaped_once(str(bot.send_document.await_args_list[0].kwargs["caption"]))


async def test_a_hostile_edition_label_is_escaped_once_too(flow):
    variants_repo.update_label(TEXT, NAME)
    cart_repo.add(USER, TEXT, 1)
    await tap(flow, "cart:view")
    assert flow.text.count(ESCAPED) == 2 and markup_problems(flow.text) == []  # product + label
