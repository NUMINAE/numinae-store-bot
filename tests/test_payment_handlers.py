"""The Stars path, driven by calling the pre-checkout and successful-payment
handlers with fake updates."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from telegram.error import Forbidden

from bot.handlers import payments
from bot.payments.registry import build_provider
from bot.repo import customers as customers_repo
from bot.repo import orders as orders_repo
from bot.repo import settings as settings_repo

from .conftest import OWNER_ID
from .helpers import add_customer, make_context, make_message, make_update, make_user
from .shop import KEY, TEXT, expire_now, key_states, pay, place_order

USER = 3001
OTHER = 3002


def stars_context(mode: str = "stars") -> Any:
    context = make_context()
    context.bot_data[payments.PROVIDER_KEY] = build_provider(mode)
    return context


def pre_checkout_query(
    user_id: int, payload: str, amount: int, currency: str = "XTR"
) -> SimpleNamespace:
    return SimpleNamespace(
        id="q1",
        from_user=make_user(user_id),
        invoice_payload=payload,
        total_amount=amount,
        currency=currency,
        answer=AsyncMock(),
    )


async def run_pre_checkout(
    context, order_id, *, user=USER, amount=None, currency="XTR", payload=None
):
    if amount is None:
        order = orders_repo.get(order_id) if isinstance(order_id, int) else None
        amount = order["total_stars"] if order else 1
    query = pre_checkout_query(user, payload or str(order_id), amount, currency)
    await payments.pre_checkout(make_update(user, pre_checkout_query=query), context)
    return query


def payment_update(user_id: int, order_id: int | str, amount: int, charge: str, currency="XTR"):
    message = make_message(None)
    message.successful_payment = SimpleNamespace(
        invoice_payload=str(order_id),
        total_amount=amount,
        currency=currency,
        telegram_payment_charge_id=charge,
        provider_payment_charge_id=charge,
    )
    return make_update(user_id, message=message)


async def run_payment(
    context, order_id, charge="ch_new", *, user=USER, amount=None, currency="XTR"
):
    if amount is None:
        amount = orders_repo.get(order_id)["total_stars"]
    update = payment_update(user, order_id, amount, charge, currency)
    await payments.successful_payment(update, context)
    while context.tasks:
        await context.tasks.pop()


def texts_to(context, chat_id: int) -> list[str]:
    return [
        str(c.kwargs["text"])
        for c in context.bot.send_message.await_args_list
        if c.kwargs.get("chat_id") == chat_id
    ]


def snapshot(order_id: int) -> tuple[Any, ...]:
    order = orders_repo.get(order_id)
    assert order is not None
    return (dict(order), len(orders_repo.list_events(order_id)))


# --- pre-checkout: acceptance -----------------------------------------------------------------


async def test_a_valid_order_is_accepted_and_its_reservation_extended_first(db, monkeypatch):
    order_id = place_order(USER, {TEXT: 1, KEY: 2})
    context = stars_context()
    calls = []
    real = orders_repo.extend_reservation

    def spy(oid, minutes):
        calls.append(f"extend:{oid}:{minutes}")
        return real(oid, minutes)

    monkeypatch.setattr(orders_repo, "extend_reservation", spy)
    query = pre_checkout_query(USER, str(order_id), 75 + 100)
    query.answer.side_effect = lambda **kw: calls.append(f"answer:{kw['ok']}")

    await payments.pre_checkout(make_update(USER, pre_checkout_query=query), context)

    assert calls == [f"extend:{order_id}:2", "answer:True"]
    query.answer.assert_awaited_once_with(ok=True)


async def test_pre_checkout_makes_no_network_calls_besides_the_answer(db):
    order_id = place_order(USER, {KEY: 1})
    context = stars_context()
    await run_pre_checkout(context, order_id)
    context.bot.send_message.assert_not_called()
    context.bot.send_invoice.assert_not_called()
    context.bot.refund_star_payment.assert_not_called()


async def test_pre_checkout_changes_nothing_but_the_expiry(db):
    order_id = place_order(USER, {KEY: 2})
    before = orders_repo.get(order_id)["status"], key_states(db)
    await run_pre_checkout(stars_context(), order_id)
    assert (orders_repo.get(order_id)["status"], key_states(db)) == before


# --- pre-checkout: one rejection test per reason ----------------------------------------------


def _reject_not_found(db, context):
    place_order(USER, {TEXT: 1})
    return {"order_id": 999999, "payload": "999999"}


def _reject_unreadable_payload(db, context):
    order_id = place_order(USER, {TEXT: 1})
    return {"order_id": order_id, "payload": "not-an-id"}


def _reject_not_owner(db, context):
    order_id = place_order(USER, {TEXT: 1})
    add_customer(OTHER)
    return {"order_id": order_id, "user": OTHER}


def _reject_not_pending(db, context):
    order_id = place_order(USER, {TEXT: 1})
    orders_repo.cancel_order(order_id, "customer")
    return {"order_id": order_id}


def _reject_expired(db, context):
    order_id = place_order(USER, {TEXT: 1})
    expire_now(db, order_id)  # past expires_at but not yet swept
    return {"order_id": order_id}


def _reject_wrong_mode(db, context):
    order_id = place_order(USER, {TEXT: 1}, mode="mock")  # but the bot runs in stars mode
    return {"order_id": order_id}


def _reject_wrong_currency(db, context):
    return {"order_id": place_order(USER, {TEXT: 1}), "currency": "USD"}


def _reject_wrong_amount(db, context):
    order_id = place_order(USER, {TEXT: 1})
    return {"order_id": order_id, "amount": 74}


def _reject_reservation_lost(db, context):
    order_id = place_order(USER, {KEY: 3})
    db.execute(
        "UPDATE license_keys SET status = 'available', order_item_id = NULL"
        " WHERE id = (SELECT MIN(id) FROM license_keys WHERE status = 'reserved')"
    )
    db.commit()
    return {"order_id": order_id}


REJECTIONS = [
    ("not_found", _reject_not_found, "This order is no longer available."),
    ("unreadable_payload", _reject_unreadable_payload, "This order is no longer available."),
    ("not_owner", _reject_not_owner, "This order is no longer available."),
    ("not_pending", _reject_not_pending, "This order is no longer open for payment."),
    ("expired", _reject_expired, "This order has expired. Please start a new one."),
    ("wrong_mode", _reject_wrong_mode, "Payments for this order are not available right now."),
    (
        "wrong_currency",
        _reject_wrong_currency,
        "Payments for this order are not available right now.",
    ),
    ("wrong_amount", _reject_wrong_amount, "The amount does not match this order."),
    (
        "reservation_lost",
        _reject_reservation_lost,
        "Your items are no longer reserved. Please start a new order.",
    ),
]


@pytest.mark.parametrize(("name", "setup", "message"), REJECTIONS, ids=[r[0] for r in REJECTIONS])
async def test_each_rejection_reason_answers_not_ok_with_its_message(db, name, setup, message):
    context = stars_context()
    kwargs = setup(db, context)
    order_id = kwargs.pop("order_id")
    state_before = (key_states(db), snapshot(order_id) if orders_repo.get(order_id) else None)

    query = await run_pre_checkout(context, order_id, **kwargs)

    query.answer.assert_awaited_once_with(ok=False, error_message=message)
    state_after = (key_states(db), snapshot(order_id) if orders_repo.get(order_id) else None)
    assert state_after == state_before  # a refusal changes nothing, not even the expiry


async def test_rejection_messages_are_translated(db):
    order_id = place_order(USER, {TEXT: 1})
    customers_repo.set_language(USER, "ru")
    orders_repo.cancel_order(order_id, "customer")
    query = await run_pre_checkout(stars_context(), order_id)
    assert query.answer.await_args.kwargs["error_message"] == (
        "Этот заказ больше не ожидает оплаты."
    )


async def test_an_unexpected_error_still_answers_not_ok_and_logs(db, monkeypatch, caplog):
    caplog.set_level(logging.ERROR)
    order_id = place_order(USER, {TEXT: 1})

    def boom(_):
        raise RuntimeError("database exploded")

    monkeypatch.setattr(orders_repo, "get", boom)
    query = await run_pre_checkout(stars_context(), order_id, amount=75)

    query.answer.assert_awaited_once()
    assert query.answer.await_args.kwargs["ok"] is False
    assert any(r.levelno == logging.ERROR for r in caplog.records)


async def test_a_failing_answer_after_an_error_does_not_raise(db, monkeypatch, caplog):
    caplog.set_level(logging.ERROR)
    order_id = place_order(USER, {TEXT: 1})
    monkeypatch.setattr(orders_repo, "get", lambda _: (_ for _ in ()).throw(RuntimeError("x")))
    query = pre_checkout_query(USER, str(order_id), 75)
    query.answer.side_effect = RuntimeError("telegram down")
    await payments.pre_checkout(make_update(USER, pre_checkout_query=query), stars_context())
    assert sum(r.levelno == logging.ERROR for r in caplog.records) >= 2


async def test_an_update_without_a_pre_checkout_query_is_ignored(db):
    await payments.pre_checkout(make_update(USER), stars_context())


# --- successful_payment: PAID -----------------------------------------------------------------


async def test_a_paid_order_notifies_both_sides_and_delivers(db):
    order_id = place_order(USER, {TEXT: 1, KEY: 2})
    context = stars_context()
    await run_payment(context, order_id, "ch_ok")

    order = orders_repo.get(order_id)
    assert (order["status"], order["telegram_payment_charge_id"]) == ("delivered", "ch_ok")
    customer = texts_to(context, USER)
    assert any("Payment received for order" in m for m in customer)
    assert any("example.com/demo/neon-prompts-personal" in m for m in customer)
    assert sum("<code>NUMI-DEMO-ORBP-" in m for m in customer) == 1
    owner = texts_to(context, OWNER_ID)
    assert len(owner) == 1
    assert f"New paid order #{order_id}" in owner[0]
    assert f"id {USER}" in owner[0] and "⭐ 175" in owner[0]
    assert "Orbit Icons Pro — Personal × 2" in owner[0]


async def test_the_receipt_is_logged_first_with_ids_only(db, caplog):
    caplog.set_level(logging.INFO)
    customers_repo.upsert(USER, "secret_username", "Secret Name")
    order_id = place_order(USER, {TEXT: 1})
    context = stars_context()
    await run_payment(context, order_id, "ch_receipt")

    records = [r for r in caplog.records if r.name == "bot.handlers.payments"]
    first = records[0].getMessage()
    assert f"order_id={order_id}" in first and f"user_id={USER}" in first
    assert "charge_id=ch_receipt" in first and "amount=75 XTR" in first
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "secret_username" not in text and "Secret Name" not in text
    assert "SimpleNamespace" not in text  # the update object is never logged


async def test_the_handler_returns_before_delivery_finishes(db):
    order_id = place_order(USER, {TEXT: 1})
    context = stars_context()
    gate = asyncio.Event()
    started = asyncio.Event()

    async def send(**kwargs):
        if "example.com/demo" in str(kwargs.get("text")):  # the delivery message
            started.set()
            await gate.wait()

    context.bot.send_message.side_effect = send
    update = payment_update(USER, order_id, 75, "ch_slow")

    await asyncio.wait_for(payments.successful_payment(update, context), timeout=2)

    assert orders_repo.get(order_id)["status"] == "paid"  # recorded, not yet delivered
    assert len(context.tasks) == 1
    await asyncio.wait_for(started.wait(), timeout=2)  # delivery is underway in the background
    assert orders_repo.get(order_id)["status"] == "paid"
    gate.set()
    await context.tasks.pop()
    assert orders_repo.get(order_id)["status"] == "delivered"


async def test_a_duplicate_successful_payment_delivers_once(db):
    order_id = place_order(USER, {TEXT: 1, KEY: 1})
    context = stars_context()
    await run_payment(context, order_id, "ch_twice")
    sent_after_first = len(context.bot.send_message.await_args_list)
    assert orders_repo.get(order_id)["status"] == "delivered"
    events = len(orders_repo.list_events(order_id))

    await run_payment(context, order_id, "ch_twice")  # Telegram redelivered the update

    assert len(context.bot.send_message.await_args_list) == sent_after_first
    assert len(orders_repo.list_events(order_id)) == events
    delivery_messages = [m for m in texts_to(context, USER) if "example.com/demo" in m]
    assert len(delivery_messages) == 1
    assert key_states(db) == {"available": 9, "sold": 1}


async def test_a_duplicate_is_logged_quietly_at_info(db, caplog):
    order_id = place_order(USER, {TEXT: 1})
    context = stars_context()
    await run_payment(context, order_id, "ch_dup")
    caplog.set_level(logging.INFO)
    caplog.clear()
    await run_payment(context, order_id, "ch_dup")
    assert any("Duplicate payment ignored" in r.getMessage() for r in caplog.records)
    assert not any(r.levelno >= logging.WARNING for r in caplog.records)


async def test_a_blocked_customer_still_gets_the_payment_recorded(db):
    order_id = place_order(USER, {TEXT: 1, KEY: 1})
    context = stars_context()

    async def send(**kwargs):
        if kwargs["chat_id"] == USER:
            raise Forbidden("Forbidden: bot was blocked by the user")

    context.bot.send_message.side_effect = send
    await run_payment(context, order_id, "ch_blocked")

    order = orders_repo.get(order_id)
    assert order["status"] == "paid"  # recorded; delivery could not reach the customer
    assert order["telegram_payment_charge_id"] == "ch_blocked"
    assert key_states(db)["sold"] == 1
    assert len(orders_repo.list_undelivered_items(order_id)) == 2
    assert any(f"New paid order #{order_id}" in m for m in texts_to(context, OWNER_ID))


async def test_a_blocked_owner_does_not_block_the_customer(db):
    order_id = place_order(USER, {TEXT: 1})
    context = stars_context()

    async def send(**kwargs):
        if kwargs["chat_id"] == OWNER_ID:
            raise Forbidden("blocked")

    context.bot.send_message.side_effect = send
    await run_payment(context, order_id)
    assert orders_repo.get(order_id)["status"] == "delivered"


# --- short key reservation at payment time ----------------------------------------------------


async def test_a_short_reservation_alerts_the_owner_immediately_and_delays_the_customer(db):
    order_id = place_order(USER, {KEY: 3})
    db.execute(
        "UPDATE license_keys SET status = 'available', order_item_id = NULL"
        " WHERE id = (SELECT MIN(id) FROM license_keys WHERE status = 'reserved')"
    )
    db.commit()
    context = stars_context()

    await run_payment(context, order_id, "ch_short")

    assert orders_repo.get(order_id)["status"] == "paid"  # PAID plus the audit note
    assert "incomplete" in orders_repo.list_events(order_id)[-1]["note"]
    customer = texts_to(context, USER)
    assert any("delivery is delayed" in m.lower() or "Delivery is delayed" in m for m in customer)
    alerts = [m for m in texts_to(context, OWNER_ID) if "keys are short" in m]
    assert len(alerts) == 1
    alert = alerts[0]
    assert f"#{order_id}" in alert and f"Buyer id: {USER}" in alert
    assert "Orbit Icons Pro — Personal × 3" in alert
    assert "ch_short" in alert and "Refund or add keys" in alert
    assert "2 of 3" in alert


# --- orphans: the refund uses the INCOMING charge id ------------------------------------------


async def test_a_payment_after_expiry_is_refunded_with_notifications(db):
    order_id = place_order(USER, {KEY: 2})
    expire_now(db, order_id)
    orders_repo.expire_due_orders()
    context = stars_context()
    context.bot.refund_star_payment.return_value = True
    before = snapshot(order_id)

    await run_payment(context, order_id, "ch_late", amount=100)

    context.bot.refund_star_payment.assert_awaited_once_with(
        user_id=USER, telegram_payment_charge_id="ch_late"
    )
    assert orders_repo.get(order_id)["status"] == "expired"
    assert orders_repo.get(order_id)["telegram_payment_charge_id"] is None
    assert snapshot(order_id)[0] == before[0]
    customer = texts_to(context, USER)
    assert any("arrived after the order expired" in m and "refunded" in m for m in customer)
    owner = texts_to(context, OWNER_ID)
    assert any("ch_late" in m and "refunded automatically" in m for m in owner)
    assert not any("example.com" in m for m in customer)  # nothing delivered


async def test_a_duplicate_charge_on_a_paid_order_is_refunded_with_the_new_charge_id(db):
    order_id = place_order(USER, {TEXT: 1})
    context = stars_context()
    context.bot.refund_star_payment.return_value = True
    await run_payment(context, order_id, "ch_original")
    original = snapshot(order_id)[0]
    sent_before = len(texts_to(context, USER))

    await run_payment(context, order_id, "ch_second_charge")

    # Refunded with the charge id from the INCOMING update, never the stored one.
    context.bot.refund_star_payment.assert_awaited_once_with(
        user_id=USER, telegram_payment_charge_id="ch_second_charge"
    )
    assert snapshot(order_id)[0] == original  # the original order is unchanged
    assert orders_repo.get(order_id)["telegram_payment_charge_id"] == "ch_original"
    new_messages = texts_to(context, USER)[sent_before:]
    assert any("duplicate payment" in m and "refunded" in m for m in new_messages)
    assert not any("expired" in m for m in new_messages)  # a different message than expiry
    assert any("ch_second_charge" in m for m in texts_to(context, OWNER_ID))
    assert len([m for m in texts_to(context, USER) if "example.com/demo" in m]) == 1


async def test_the_two_orphan_reasons_use_different_customer_messages(db):
    expired_order = place_order(USER, {TEXT: 1})
    expire_now(db, expired_order)
    orders_repo.expire_due_orders()
    paid_order = place_order(OTHER, {2: 1})
    context = stars_context()
    context.bot.refund_star_payment.return_value = True
    await run_payment(context, paid_order, "ch_p1", user=OTHER)
    context.bot.send_message.reset_mock()

    await run_payment(context, expired_order, "ch_e1")
    await run_payment(context, paid_order, "ch_p2", user=OTHER)

    assert "expired" in texts_to(context, USER)[0]
    assert "duplicate payment" in texts_to(context, OTHER)[0]


@pytest.mark.parametrize("failure", ["returns_false", "raises_telegram_error", "provider_raises"])
async def test_a_failed_refund_alerts_the_owner_loudly(db, caplog, failure):
    caplog.set_level(logging.ERROR)
    order_id = place_order(USER, {TEXT: 1})
    context = stars_context()
    await run_payment(context, order_id, "ch_a")
    if failure == "returns_false":
        context.bot.refund_star_payment.return_value = False
    elif failure == "raises_telegram_error":
        from telegram.error import BadRequest

        context.bot.refund_star_payment.side_effect = BadRequest("CHARGE_ALREADY_REFUNDED")
    else:
        provider = SimpleNamespace(mode="stars", refund=AsyncMock(side_effect=RuntimeError("x")))
        context.bot_data[payments.PROVIDER_KEY] = provider

    await run_payment(context, order_id, "ch_b")

    assert any(r.levelno == logging.ERROR and "ch_b" in r.getMessage() for r in caplog.records)
    owner = texts_to(context, OWNER_ID)
    assert any("REFUND FAILED" in m and "ch_b" in m and f"#{order_id}" in m for m in owner)
    assert any("automatic refund failed" in m for m in texts_to(context, USER))
    assert orders_repo.get(order_id)["telegram_payment_charge_id"] == "ch_a"


# --- mismatches -------------------------------------------------------------------------------


@pytest.mark.parametrize("cause", ["amount", "currency", "other_user", "missing_order"])
async def test_a_mismatched_payment_delivers_nothing_and_alerts_the_owner(db, caplog, cause):
    caplog.set_level(logging.ERROR)
    order_id = place_order(USER, {TEXT: 1, KEY: 2})
    add_customer(OTHER)
    context = stars_context()
    kwargs = {"order_id": order_id, "charge": "ch_bad"}
    if cause == "amount":
        kwargs["amount"] = 1
    elif cause == "currency":
        kwargs["currency"] = "USD"
    elif cause == "other_user":
        kwargs["user"] = OTHER
    else:
        kwargs["order_id"] = 999999
        kwargs["amount"] = 175
    before = (snapshot(order_id), key_states(db))

    await run_payment(context, **kwargs)

    assert (snapshot(order_id), key_states(db)) == before
    assert not any("example.com" in m or "<code>" in m for m in texts_to(context, USER))
    context.bot.refund_star_payment.assert_not_called()
    assert any(r.levelno == logging.ERROR and "ch_bad" in r.getMessage() for r in caplog.records)
    alerts = texts_to(context, OWNER_ID)
    assert len(alerts) == 1
    assert "Payment could not be matched" in alerts[0] and "ch_bad" in alerts[0]
    assert f"#{kwargs['order_id']}" in alerts[0]


async def test_an_unreadable_payload_alerts_the_owner_and_does_nothing_else(db):
    place_order(USER, {TEXT: 1})
    context = stars_context()
    update = payment_update(USER, "garbage", 75, "ch_x")
    await payments.successful_payment(update, context)
    alerts = texts_to(context, OWNER_ID)
    assert len(alerts) == 1 and "unreadable payload" in alerts[0] and "ch_x" in alerts[0]
    assert context.tasks == []


async def test_a_message_without_a_payment_is_ignored(db):
    context = stars_context()
    await payments.successful_payment(make_update(USER, message=make_message("hi")), context)
    context.bot.send_message.assert_not_called()


# --- /paysupport ------------------------------------------------------------------------------


async def test_paysupport_replies_with_the_escaped_support_note(db):
    settings_repo.set("support_note", "Mail <b>help@example.com</b> & quote it")
    message = make_message("/paysupport")
    await payments.paysupport(make_update(USER, message=message), stars_context())
    (text,) = [c.args[0] for c in message.reply_text.await_args_list]
    assert "quote your order number" in text.lower()
    assert "&lt;b&gt;help@example.com&lt;/b&gt; &amp; quote it" in text


async def test_paysupport_is_localised(db):
    add_customer(USER)
    customers_repo.set_language(USER, "ru")
    message = make_message("/paysupport")
    await payments.paysupport(make_update(USER, message=message), stars_context())
    assert "номер заказа" in message.reply_text.await_args.args[0]


def test_parse_order_id():
    assert payments.parse_order_id("42") == 42
    for bad in ("", "abc", "-1", "0", "4.5", str(2**63), "1 2"):
        assert payments.parse_order_id(bad) is None


def test_pay_helper_still_works_with_the_delivery_task_free_path(db):
    order_id = place_order(USER, {TEXT: 1})
    assert pay(order_id).value == "paid"
