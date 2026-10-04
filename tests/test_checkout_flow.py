"""Checkout through the real handlers: the full mock-mode journey, the guards,
cancel/resume, and the three refusals of the demo payment."""

from __future__ import annotations

import logging
import re

import pytest

from bot.handlers import cart, catalogue, checkout, library, payments
from bot.repo import cart as cart_repo
from bot.repo import orders as orders_repo
from bot.repo import variants as variants_repo
from bot.repo.orders import EmptyCartError, InsufficientStock, OpenOrderExists

from .conftest import OWNER_ID
from .flow import Flow
from .helpers import add_customer
from .shop import KEY, TEXT, expire_now, fill_cart, key_states, place_order

USER = 3001
OTHER = 3002
UNAVAILABLE_ALERT = "Demo payments are not available."
GENERIC = "That order is no longer available."


@pytest.fixture
def flow(db, monkeypatch):
    add_customer(USER)
    return Flow(monkeypatch, USER, mode="mock")


def sent_markup_data(flow: Flow) -> list[str]:
    markup = flow.sent_messages()[-1].kwargs["reply_markup"]
    return [str(b.callback_data) for row in markup.inline_keyboard for b in row]


def texts_to(flow: Flow, chat_id: int) -> list[str]:
    return [
        str(c.kwargs["text"])
        for c in flow.sent_messages()
        if c.kwargs.get("chat_id") == chat_id and "text" in c.kwargs
    ]


# --- the full journey -------------------------------------------------------------------------


async def test_full_mock_journey_browse_to_library_and_resend(flow, db):
    # browse -> add a text edition and three keys
    await flow.tap(catalogue.route, "cat:cats")
    await flow.tap(catalogue.route, f"cat:add:{TEXT}:0:1")
    await flow.tap(catalogue.route, f"cat:add:{KEY}:0:3")

    # cart -> checkout review
    await flow.tap(cart.route, "cart:view")
    assert "Subtotal: ⭐ 225" in flow.text
    await flow.tap(checkout.route, flow.data_for("Checkout"))
    assert "Step 1 of 3 · Review" in flow.text
    assert "Neon Portrait Prompt Pack — Personal × 1 — ⭐ 75" in flow.text
    assert "Total: ⭐ 225" in flow.text
    assert "Demo mode: no real payment" in flow.text

    # pay -> the order exists and the demo summary was sent
    await flow.tap(checkout.route, flow.data_for("Pay ⭐ 225"))
    order = orders_repo.get_open_for_user(USER)
    assert order is not None and order["payment_mode"] == "mock"
    assert key_states(db) == {"available": 7, "reserved": 3}
    summary = texts_to(flow, USER)[-1]
    assert "DEMO MODE" in summary and "⭐ 225" in summary
    mock_pay = f"co:mockpay:{order['id']}"
    assert mock_pay in sent_markup_data(flow)

    # mock pay -> recorded, delivered
    await flow.tap(checkout.route, mock_pay)
    await flow.drain()
    order = orders_repo.get(order["id"])
    assert order["status"] == "delivered"
    assert order["telegram_payment_charge_id"].startswith("mock_")
    delivered = "\n".join(texts_to(flow, USER))
    assert "example.com/demo/neon-prompts-personal" in delivered
    keys = re.findall(r"<code>(NUMI-DEMO-ORBP-\d{4})</code>", delivered)
    assert len(keys) == 3 and len(set(keys)) == 3  # three distinct keys, each in <code>
    assert f"Order #{order['id']} complete" in delivered
    assert any(f"New paid order #{order['id']}" in m for m in texts_to(flow, OWNER_ID))

    # My Library -> item -> send again
    await flow.tap(library.route, "lib:list:0")
    assert len(flow.buttons) >= 3 and any("Neon Portrait" in label for label, _ in flow.buttons)
    await flow.tap(library.route, flow.data_for("Orbit Icons Pro"))
    assert f"Order #{order['id']}" in flow.text and "Delivered" in flow.text
    before = len(flow.sent_messages())
    await flow.tap(library.route, flow.data_for("Send again"))
    assert len(flow.sent_messages()) == before + 1
    assert re.findall(r"<code>(NUMI-DEMO-ORBP-\d{4})</code>", texts_to(flow, USER)[-1]) == keys
    assert orders_repo.get(order["id"])["status"] == "delivered"  # re-send changed nothing


async def test_the_purchased_keys_are_sold_and_the_cart_is_empty_afterwards(flow, db):
    fill_cart(USER, {KEY: 2})
    await flow.tap(checkout.route, "co:pay")
    order = orders_repo.get_open_for_user(USER)
    await flow.tap(checkout.route, f"co:mockpay:{order['id']}")
    await flow.drain()
    assert key_states(db) == {"available": 8, "sold": 2}
    assert cart_repo.list_lines(USER) == []


# --- guards -----------------------------------------------------------------------------------


async def test_an_empty_cart_cannot_check_out(flow):
    await flow.tap(checkout.route, "co:start")
    assert "nothing in your cart" in flow.text
    assert "Browse products" in flow.labels() and flow.data_for("‹ Back") == "cart:view"


async def test_a_cart_of_only_unavailable_lines_cannot_check_out(flow):
    fill_cart(USER, {TEXT: 1})
    variants_repo.set_active(TEXT, False)
    await flow.tap(checkout.route, "co:start")
    assert "nothing in your cart" in flow.text


async def test_an_open_order_offers_resume_and_cancel_with_whole_minutes(flow, db):
    order_id = place_order(USER, {TEXT: 1}, mode="mock")
    await flow.tap(checkout.route, "co:start")
    assert f"open order (#{order_id})" in flow.text
    assert re.search(r"expires in (1[0-5]|[1-9]) min", flow.text)
    datas = [d for _, d in flow.buttons]
    assert f"co:resume:{order_id}" in datas and f"co:cancel:{order_id}" in datas


async def test_the_review_shows_no_demo_notice_in_stars_mode(db, monkeypatch):
    add_customer(USER)
    stars_flow = Flow(monkeypatch, USER, mode="stars")
    fill_cart(USER, {TEXT: 1})
    await stars_flow.tap(checkout.route, "co:start")
    assert "Demo mode" not in stars_flow.text and "Step 1 of 3 · Review" in stars_flow.text


async def test_dropped_lines_are_mentioned_in_one_line(flow):
    fill_cart(USER, {TEXT: 1, 2: 1})
    variants_repo.set_active(2, False)
    await flow.tap(checkout.route, "co:start")
    assert "1 unavailable item was left out of this order." in flow.text
    await flow.tap(checkout.route, "co:pay")
    assert "1 unavailable item was left out of this order." in flow.text
    assert "ready" in flow.text


# --- create_order failures map to friendly screens --------------------------------------------


async def test_a_race_for_the_open_order_shows_the_open_order_screen(flow, monkeypatch):
    existing = place_order(USER, {TEXT: 1}, mode="mock")
    fill_cart(USER, {KEY: 1})

    def raise_open(user_id, mode):
        raise OpenOrderExists(existing)

    monkeypatch.setattr(orders_repo, "create_order", raise_open)
    await flow.tap(checkout.route, "co:pay")
    assert f"open order (#{existing})" in flow.text


async def test_insufficient_stock_and_empty_cart_are_mapped(flow, monkeypatch):
    fill_cart(USER, {KEY: 1})
    monkeypatch.setattr(
        orders_repo, "create_order", lambda u, m: (_ for _ in ()).throw(InsufficientStock(KEY, 2))
    )
    await flow.tap(checkout.route, "co:pay")
    assert "Only 2 left" in flow.text and flow.data_for("‹ Back") == "cart:view"

    monkeypatch.setattr(
        orders_repo, "create_order", lambda u, m: (_ for _ in ()).throw(EmptyCartError())
    )
    await flow.tap(checkout.route, "co:pay")
    assert "nothing in your cart" in flow.text


async def test_a_provider_failure_cancels_the_order_and_restores_the_cart(flow, db, caplog):
    caplog.set_level(logging.ERROR)
    fill_cart(USER, {TEXT: 1, KEY: 2})
    flow.context.bot.send_message.side_effect = RuntimeError("telegram is down")
    await flow.tap(checkout.route, "co:pay")

    assert "couldn&#x27;t start the payment" in flow.text
    assert orders_repo.get_open_for_user(USER) is None
    assert key_states(db) == {"available": 10}
    assert sorted(ln["variant_id"] for ln in cart_repo.list_lines(USER)) == [TEXT, KEY]
    assert any(r.levelno == logging.ERROR for r in caplog.records)


async def test_stars_mode_sends_an_invoice_with_the_order_id_payload(db, monkeypatch):
    add_customer(USER)
    stars_flow = Flow(monkeypatch, USER, mode="stars")
    fill_cart(USER, {TEXT: 1})
    await stars_flow.tap(checkout.route, "co:pay")
    order = orders_repo.get_open_for_user(USER)
    (call,) = stars_flow.context.bot.send_invoice.await_args_list
    assert call.kwargs["payload"] == str(order["id"]) and call.kwargs["currency"] == "XTR"
    assert order["payment_mode"] == "stars"


# --- resume -----------------------------------------------------------------------------------


async def test_resume_re_presents_the_checkout(flow):
    order_id = place_order(USER, {TEXT: 1}, mode="mock")
    await flow.tap(checkout.route, f"co:resume:{order_id}")
    assert f"co:mockpay:{order_id}" in sent_markup_data(flow)
    assert "ready" in flow.text


async def test_resuming_an_order_from_another_mode_cancels_it_and_says_to_restart(flow, db):
    order_id = place_order(USER, {TEXT: 1, KEY: 2}, mode="stars")  # the bot now runs in mock mode
    await flow.tap(checkout.route, f"co:resume:{order_id}")

    assert "different payment mode" in flow.text
    assert orders_repo.get(order_id)["status"] == "cancelled"
    assert key_states(db) == {"available": 10}  # keys released
    assert len(cart_repo.list_lines(USER)) == 2  # and the cart restored
    flow.context.bot.send_message.assert_not_called()


async def test_resuming_an_expired_but_unswept_order_is_refused(flow, db):
    order_id = place_order(USER, {TEXT: 1}, mode="mock")
    expire_now(db, order_id)
    await flow.tap(checkout.route, f"co:resume:{order_id}")
    assert flow.alert == "This order has expired. Please start a new one."
    flow.context.bot.send_message.assert_not_called()


async def test_resuming_a_settled_order_is_refused(flow):
    order_id = place_order(USER, {TEXT: 1}, mode="mock")
    orders_repo.cancel_order(order_id, "customer")
    await flow.tap(checkout.route, f"co:resume:{order_id}")
    assert flow.alert == "This order can't be cancelled any more."


# --- cancel -----------------------------------------------------------------------------------


async def test_cancel_asks_first_and_no_keeps_the_order(flow):
    order_id = place_order(USER, {TEXT: 1}, mode="mock")
    await flow.tap(checkout.route, f"co:cancel:{order_id}")
    assert f"Cancel order #{order_id}?" in flow.text
    assert orders_repo.get(order_id)["status"] == "pending_payment"

    await flow.tap(checkout.route, flow.data_for("No, keep it"))
    assert orders_repo.get(order_id)["status"] == "pending_payment"
    assert "open order" in flow.text


async def test_cancel_yes_cancels_releases_keys_and_restores_the_cart(flow, db):
    order_id = place_order(USER, {TEXT: 1, KEY: 3}, mode="mock")
    await flow.tap(checkout.route, f"co:cancel:{order_id}")
    await flow.tap(checkout.route, flow.data_for("Yes, cancel"))

    assert orders_repo.get(order_id)["status"] == "cancelled"
    assert key_states(db) == {"available": 10}
    assert "back in your cart" in flow.text
    assert sorted((ln["variant_id"], ln["quantity"]) for ln in cart_repo.list_lines(USER)) == [
        (TEXT, 1),
        (KEY, 3),
    ]

    await flow.tap(checkout.route, f"co:cancelyes:{order_id}")  # double tap
    assert flow.alert == "This order can't be cancelled any more."


# --- the demo payment: three refusals ---------------------------------------------------------


async def test_mock_pay_is_unreachable_in_stars_mode(db, monkeypatch):
    add_customer(USER)
    stars_flow = Flow(monkeypatch, USER, mode="stars")
    for order_mode in ("mock", "stars"):
        order_id = place_order(USER, {TEXT: 1}, mode=order_mode)
        before = dict(orders_repo.get(order_id))
        await stars_flow.tap(checkout.route, f"co:mockpay:{order_id}")
        assert stars_flow.alert == UNAVAILABLE_ALERT
        assert dict(orders_repo.get(order_id)) == before
        assert len(orders_repo.list_events(order_id)) == 1
        orders_repo.cancel_order(order_id, "customer")
        cart_repo.clear(USER)
    stars_flow.edit.assert_not_called()
    stars_flow.context.bot.send_message.assert_not_called()


async def test_mock_pay_refuses_an_order_created_in_stars_mode(flow, db):
    order_id = place_order(USER, {KEY: 2}, mode="stars")
    before = (dict(orders_repo.get(order_id)), key_states(db))
    await flow.tap(checkout.route, f"co:mockpay:{order_id}")
    assert flow.alert == UNAVAILABLE_ALERT
    assert (dict(orders_repo.get(order_id)), key_states(db)) == before


async def test_mock_pay_on_someone_elses_order_is_refused_like_a_missing_one(flow, db, monkeypatch):
    order_id = place_order(OTHER, {KEY: 2}, mode="mock")
    before = (dict(orders_repo.get(order_id)), key_states(db))

    await flow.tap(checkout.route, f"co:mockpay:{order_id}")
    foreign = flow.alert
    probe = Flow(monkeypatch, USER, mode="mock")
    await probe.tap(checkout.route, "co:mockpay:999999")

    assert foreign == probe.alert == GENERIC
    assert (dict(orders_repo.get(order_id)), key_states(db)) == before
    assert orders_repo.get(order_id)["status"] == "pending_payment"


async def test_mock_pay_on_an_expired_but_unswept_order_is_refused_with_no_state_change(flow, db):
    order_id = place_order(USER, {KEY: 2}, mode="mock")
    expire_now(db, order_id)
    before = (
        dict(orders_repo.get(order_id)),
        key_states(db),
        len(orders_repo.list_events(order_id)),
    )

    await flow.tap(checkout.route, f"co:mockpay:{order_id}")

    assert flow.alert == "This order has expired. Please start a new one."
    after = (
        dict(orders_repo.get(order_id)),
        key_states(db),
        len(orders_repo.list_events(order_id)),
    )
    assert after == before
    flow.context.bot.send_message.assert_not_called()


async def test_mock_pay_on_a_settled_order_and_a_lost_reservation_are_refused(flow, db):
    settled = place_order(USER, {TEXT: 1}, mode="mock")
    orders_repo.cancel_order(settled, "customer")
    await flow.tap(checkout.route, f"co:mockpay:{settled}")
    assert flow.alert == "This order is no longer open for payment."

    cart_repo.clear(USER)
    order_id = place_order(USER, {KEY: 3}, mode="mock")
    db.execute(
        "UPDATE license_keys SET status = 'available', order_item_id = NULL"
        " WHERE id = (SELECT MIN(id) FROM license_keys WHERE status = 'reserved')"
    )
    db.commit()
    await flow.tap(checkout.route, f"co:mockpay:{order_id}")
    assert flow.alert == "Your items are no longer reserved. Please start a new order."
    assert orders_repo.get(order_id)["status"] == "pending_payment"


async def test_mock_pay_extends_the_reservation_then_uses_the_shared_completion_path(
    flow, monkeypatch
):
    order_id = place_order(USER, {TEXT: 1}, mode="mock")
    calls = []
    real_extend = orders_repo.extend_reservation
    real_process = payments.process_payment

    def extend(oid, minutes):
        calls.append(f"extend:{oid}:{minutes}")
        return real_extend(oid, minutes)

    async def process(context, oid, payer, charge, amount, currency):
        calls.append(f"process:{oid}:{payer}:{amount}:{currency}:{charge.startswith('mock_')}")
        return await real_process(context, oid, payer, charge, amount, currency)

    monkeypatch.setattr(orders_repo, "extend_reservation", extend)
    monkeypatch.setattr(payments, "process_payment", process)

    await flow.tap(checkout.route, f"co:mockpay:{order_id}")
    await flow.drain()

    assert calls == [f"extend:{order_id}:2", f"process:{order_id}:{USER}:75:XTR:True"]
    assert orders_repo.get(order_id)["status"] == "delivered"


async def test_a_second_mock_pay_tap_is_a_noop(flow):
    order_id = place_order(USER, {TEXT: 1}, mode="mock")
    await flow.tap(checkout.route, f"co:mockpay:{order_id}")
    await flow.drain()
    sent = len(flow.sent_messages())
    await flow.tap(checkout.route, f"co:mockpay:{order_id}")
    assert flow.alert == "This order is no longer open for payment."
    assert len(flow.sent_messages()) == sent
