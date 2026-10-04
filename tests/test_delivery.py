"""bot/delivery.py with a fake bot."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.error import Forbidden, NetworkError

from bot import delivery
from bot.repo import orders as orders_repo
from bot.repo import variants as variants_repo

from .shop import KEY, TEXT, pay, place_order

USER = 3001
HOSTILE = "<b>&"


def fake_bot() -> SimpleNamespace:
    return SimpleNamespace(send_message=AsyncMock(), send_document=AsyncMock())


def paid_order(lines: dict[int, int], user: int = USER) -> int:
    order_id = place_order(user, lines)
    pay(order_id)
    return order_id


def _file_edition(db) -> int:
    """Turn demo edition 2 into a deliverable file edition."""
    db.execute(
        "UPDATE product_variants SET delivery_kind = 'file', delivery_file_id = 'FILE123',"
        " delivery_text = NULL WHERE id = 2"
    )
    db.commit()
    return 2


def _texts(bot) -> list[str]:
    return [c.kwargs["text"] for c in bot.send_message.await_args_list]


# --- each kind --------------------------------------------------------------------------------


async def test_text_item_sends_the_delivery_text(db):
    order_id = paid_order({TEXT: 1})
    bot = fake_bot()
    result = await delivery.deliver_order(bot, order_id)

    assert result.failed == [] and len(result.delivered) == 1
    first = bot.send_message.await_args_list[0].kwargs
    assert first["chat_id"] == USER
    assert "example.com/demo/neon-prompts-personal" in first["text"]
    assert "Neon Portrait Prompt Pack — Personal" in first["text"]


async def test_file_item_sends_a_document_by_file_id(db):
    edition = _file_edition(db)
    order_id = paid_order({edition: 1})
    bot = fake_bot()
    await delivery.deliver_order(bot, order_id)
    (call,) = bot.send_document.await_args_list
    assert call.kwargs["document"] == "FILE123" and call.kwargs["chat_id"] == USER
    assert "Neon Portrait Prompt Pack — Commercial" in call.kwargs["caption"]


async def test_key_item_sends_every_sold_key_in_code_tags(db):
    order_id = paid_order({KEY: 3})
    bot = fake_bot()
    await delivery.deliver_order(bot, order_id)
    text = _texts(bot)[0]
    assert text.count("<code>NUMI-DEMO-ORBP-") == 3
    assert text.count("</code>") == 3


async def test_all_delivered_moves_the_order_and_sends_one_receipt(db):
    order_id = paid_order({TEXT: 1, KEY: 2})
    bot = fake_bot()
    result = await delivery.deliver_order(bot, order_id)

    assert len(result.delivered) == 2 and result.failed == []
    order, items = orders_repo.get_with_items(order_id)
    assert order["status"] == "delivered" and order["delivered_at"] is not None
    assert all(i["delivered_at"] is not None for i in items)
    receipt = _texts(bot)[-1]
    assert f"Order #{order_id}" in receipt
    assert "Orbit Icons Pro — Personal × 2" in receipt and "⭐ 175" in receipt
    assert len(_texts(bot)) == 3  # two items + the receipt


async def test_a_second_run_sends_nothing(db):
    order_id = paid_order({TEXT: 1})
    bot = fake_bot()
    await delivery.deliver_order(bot, order_id)
    again = fake_bot()
    assert await delivery.deliver_order(again, order_id) == ([], [])
    again.send_message.assert_not_called()


@pytest.mark.parametrize("status", ["pending_payment", "cancelled", "refunded"])
async def test_orders_that_are_not_paid_are_never_delivered(db, status):
    order_id = place_order(USER, {TEXT: 1})
    if status == "cancelled":
        orders_repo.cancel_order(order_id, "customer")
    elif status == "refunded":
        pay(order_id)
        orders_repo.set_status(order_id, "refunded", "owner")
    bot = fake_bot()
    assert await delivery.deliver_order(bot, order_id) == ([], [])
    bot.send_message.assert_not_called()
    assert await delivery.deliver_order(bot, 999999) == ([], [])


# --- failures ---------------------------------------------------------------------------------


async def test_partial_failure_resumes_only_the_failed_item(db, caplog):
    caplog.set_level(logging.ERROR)
    order_id = paid_order({TEXT: 1, KEY: 2})
    text_item, key_item = orders_repo.get_with_items(order_id)[1]
    bot = fake_bot()
    # The text message fails to send; the key message goes through.
    bot.send_message.side_effect = [Forbidden("bot was blocked"), None, None]

    first = await delivery.deliver_order(bot, order_id)

    assert first.failed == [text_item["id"]] and first.delivered == [key_item["id"]]
    assert orders_repo.get(order_id)["status"] == "paid"  # not complete yet
    assert orders_repo.list_undelivered_items(order_id)[0]["id"] == text_item["id"]
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert any(
        f"order_id={order_id}" in m and f"order_item_id={text_item['id']}" in m for m in errors
    )

    second_bot = fake_bot()
    second = await delivery.deliver_order(second_bot, order_id)
    assert second.delivered == [text_item["id"]] and second.failed == []
    assert len(_texts(second_bot)) == 2  # only the text item again + the receipt
    assert not any("<code>" in t for t in _texts(second_bot))  # the keys were not re-sent
    assert orders_repo.get(order_id)["status"] == "delivered"


async def test_no_receipt_and_no_status_change_while_an_item_is_missing(db):
    order_id = paid_order({TEXT: 1})
    bot = fake_bot()
    bot.send_message.side_effect = NetworkError("down")
    result = await delivery.deliver_order(bot, order_id)
    assert result.delivered == [] and len(result.failed) == 1
    assert orders_repo.get(order_id)["status"] == "paid"
    assert len(bot.send_message.await_args_list) == 1  # no receipt attempt


async def test_a_failing_receipt_does_not_undo_delivery(db, caplog):
    caplog.set_level(logging.ERROR)
    order_id = paid_order({TEXT: 1})
    bot = fake_bot()
    bot.send_message.side_effect = [None, NetworkError("down")]
    result = await delivery.deliver_order(bot, order_id)
    assert result.failed == []
    assert orders_repo.get(order_id)["status"] == "delivered"
    assert any("Receipt send failed" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("blank", [None, "", "   \n"])
async def test_empty_text_content_is_reported_failed_and_sends_nothing(db, blank, caplog):
    caplog.set_level(logging.ERROR)
    order_id = paid_order({TEXT: 1})
    db.execute("UPDATE product_variants SET delivery_text = ? WHERE id = ?", (blank, TEXT))
    db.commit()
    bot = fake_bot()
    result = await delivery.deliver_order(bot, order_id)
    assert len(result.failed) == 1 and result.delivered == []
    bot.send_message.assert_not_called()
    assert orders_repo.get(order_id)["status"] == "paid"
    assert any(r.levelno == logging.ERROR for r in caplog.records)


async def test_missing_file_id_and_missing_keys_are_failures(db):
    edition = _file_edition(db)
    file_order = paid_order({edition: 1}, user=3002)
    db.execute("UPDATE product_variants SET delivery_file_id = '' WHERE id = ?", (edition,))
    key_order = paid_order({KEY: 2}, user=3003)
    db.execute("UPDATE license_keys SET status = 'revoked' WHERE status = 'sold'")
    db.commit()
    bot = fake_bot()
    assert len((await delivery.deliver_order(bot, file_order)).failed) == 1
    assert len((await delivery.deliver_order(bot, key_order)).failed) == 1
    bot.send_message.assert_not_called()
    bot.send_document.assert_not_called()


async def test_a_deleted_variant_cannot_be_delivered(db):
    order_id = paid_order({TEXT: 1})
    db.execute("DELETE FROM product_variants WHERE id = ?", (TEXT,))  # ON DELETE SET NULL
    db.commit()
    bot = fake_bot()
    assert len((await delivery.deliver_order(bot, order_id)).failed) == 1


async def test_a_deactivated_edition_still_delivers_to_a_paying_customer(db):
    order_id = paid_order({TEXT: 1})
    variants_repo.set_active(TEXT, False)
    from bot.repo import categories as categories_repo
    from bot.repo import products as products_repo

    products_repo.set_active(1, False)
    categories_repo.set_active(1, False)
    bot = fake_bot()
    result = await delivery.deliver_order(bot, order_id)
    assert result.failed == [] and len(result.delivered) == 1
    assert orders_repo.get(order_id)["status"] == "delivered"


# --- re-send ----------------------------------------------------------------------------------


async def test_resend_sends_again_without_touching_state(db):
    order_id = paid_order({TEXT: 1})
    await delivery.deliver_order(fake_bot(), order_id)
    (item,) = orders_repo.get_with_items(order_id)[1]
    stamp = item["delivered_at"]
    order_before = dict(orders_repo.get(order_id))
    events_before = len(orders_repo.list_events(order_id))

    bot = fake_bot()
    assert await delivery.deliver_item(bot, item["id"]) is True

    assert len(_texts(bot)) == 1
    assert orders_repo.get_with_items(order_id)[1][0]["delivered_at"] == stamp
    assert dict(orders_repo.get(order_id)) == order_before
    assert len(orders_repo.list_events(order_id)) == events_before


async def test_resend_of_a_paid_but_undelivered_item_does_not_stamp_it(db):
    order_id = paid_order({TEXT: 1})
    (item,) = orders_repo.get_with_items(order_id)[1]
    assert await delivery.deliver_item(fake_bot(), item["id"]) is True
    assert orders_repo.get_with_items(order_id)[1][0]["delivered_at"] is None
    assert orders_repo.get(order_id)["status"] == "paid"


async def test_resend_refuses_refunded_pending_and_unknown_items(db):
    order_id = paid_order({TEXT: 1})
    (item,) = orders_repo.get_with_items(order_id)[1]
    orders_repo.set_status(order_id, "refunded", "owner")
    bot = fake_bot()
    assert await delivery.deliver_item(bot, item["id"]) is False
    assert await delivery.deliver_item(bot, 999999) is False
    pending = place_order(3002, {TEXT: 1})
    (pending_item,) = orders_repo.get_with_items(pending)[1]
    assert await delivery.deliver_item(bot, pending_item["id"]) is False
    bot.send_message.assert_not_called()


async def test_resend_failure_returns_false_and_logs(db, caplog):
    caplog.set_level(logging.ERROR)
    order_id = paid_order({TEXT: 1})
    await delivery.deliver_order(fake_bot(), order_id)
    (item,) = orders_repo.get_with_items(order_id)[1]
    bot = fake_bot()
    bot.send_message.side_effect = Forbidden("blocked")
    assert await delivery.deliver_item(bot, item["id"]) is False
    assert any("Re-send failed" in r.getMessage() for r in caplog.records)


# --- escaping ----------------------------------------------------------------------


async def test_hostile_delivery_text_is_escaped(db):
    order_id = place_order(USER, {TEXT: 1})
    pay(order_id)
    variants_repo.set_delivery_text(TEXT, HOSTILE)
    bot = fake_bot()
    await delivery.deliver_order(bot, order_id)
    body = _texts(bot)[0]
    assert "&lt;b&gt;&amp;" in body
    assert "<b>&" not in body.replace("<b>Neon Portrait Prompt Pack — Personal</b>", "")


async def test_hostile_keys_and_names_are_escaped_in_code_tags_and_receipt(db):
    order_id = place_order(USER, {KEY: 1})
    pay(order_id)
    db.execute("UPDATE license_keys SET key_value = ? WHERE status = 'sold'", (HOSTILE,))
    db.execute("UPDATE order_items SET product_name = ?", (HOSTILE,))
    db.commit()
    bot = fake_bot()
    await delivery.deliver_order(bot, order_id)
    keys_message, receipt = _texts(bot)
    assert "<code>&lt;b&gt;&amp;</code>" in keys_message
    assert "&lt;b&gt;&amp; — Personal" in receipt
    for message in (keys_message, receipt):
        assert "<b>&" not in message.replace("<b>&lt;", "")


# --- the in-flight guard ----------------------------------------------------------------------


async def test_overlapping_deliveries_send_each_item_exactly_once(db):
    import asyncio

    order_id = paid_order({TEXT: 1, KEY: 2})
    bot = fake_bot()
    gate = asyncio.Event()
    entered = asyncio.Event()

    async def slow_send(**kwargs):
        entered.set()
        await gate.wait()

    bot.send_message.side_effect = slow_send

    first = asyncio.ensure_future(delivery.deliver_order(bot, order_id))
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        try:
            # The guard returns at once. Without it this call would block on the gate
            # behind the first one, so the short timeout turns a missing guard into a
            # fast failure rather than a hang.
            second = await asyncio.wait_for(delivery.deliver_order(bot, order_id), timeout=1)
        except TimeoutError:
            pytest.fail("the overlapping call did not return at once: the in-flight guard is gone")
    finally:
        gate.set()

    assert second == ([], [])  # returned at once, sent nothing
    assert len(bot.send_message.await_args_list) == 1  # only the first call's first item so far
    result = await first
    assert len(result.delivered) == 2 and result.failed == []
    texts = _texts(bot)
    assert sum("example.com/demo" in t for t in texts) == 1
    assert sum("<code>" in t for t in texts) == 1
    assert orders_repo.get(order_id)["status"] == "delivered"


async def test_the_guard_is_released_after_a_failure_and_after_an_exception(db, monkeypatch):
    order_id = paid_order({TEXT: 1})
    bot = fake_bot()
    bot.send_message.side_effect = Forbidden("blocked")
    assert len((await delivery.deliver_order(bot, order_id)).failed) == 1
    assert order_id not in delivery._in_flight

    # An exception escaping the delivery pass itself must release it too.
    async def exploding(bot, order_id):
        raise RuntimeError("unexpected")

    with monkeypatch.context() as patch:
        patch.setattr(delivery, "_deliver_order", exploding)
        with pytest.raises(RuntimeError):
            await delivery.deliver_order(fake_bot(), order_id)
    assert order_id not in delivery._in_flight

    retry = fake_bot()  # the next call works normally
    assert len((await delivery.deliver_order(retry, order_id)).delivered) == 1


async def test_the_guard_is_per_order(db):
    import asyncio

    first = paid_order({TEXT: 1}, user=3001)
    second = paid_order({2: 1}, user=3002)
    bot = fake_bot()
    gate = asyncio.Event()

    async def slow_send(**kwargs):
        await gate.wait()

    bot.send_message.side_effect = slow_send
    running = asyncio.ensure_future(delivery.deliver_order(bot, first))
    await asyncio.sleep(0)
    other = asyncio.ensure_future(delivery.deliver_order(bot, second))
    await asyncio.sleep(0.01)
    assert delivery._in_flight == {first, second}
    gate.set()
    await asyncio.gather(running, other)
    assert delivery._in_flight == set()


# --- the order leaving 'paid' mid-delivery --------------------------------------------


async def test_a_refund_landing_mid_delivery_stops_it_quietly(db, caplog):
    import asyncio

    caplog.set_level(logging.INFO)
    order_id = paid_order({TEXT: 1, 2: 1})  # two items
    first, second = orders_repo.get_with_items(order_id)[1]
    bot = fake_bot()
    gate = asyncio.Event()
    entered = asyncio.Event()

    async def blocked_send(**kwargs):
        entered.set()
        await gate.wait()

    bot.send_message.side_effect = blocked_send
    running = asyncio.ensure_future(delivery.deliver_order(bot, order_id))
    await asyncio.wait_for(entered.wait(), timeout=2)

    orders_repo.mark_refunded(order_id, "owner")  # the refund lands while the send is blocked
    gate.set()
    result = await running  # no exception

    assert result == ([], [])  # nothing counted as delivered
    order = orders_repo.get(order_id)
    assert order["status"] == "refunded"  # never 'delivered'
    assert len(bot.send_message.await_args_list) == 1  # the second item was never sent
    items = orders_repo.get_with_items(order_id)[1]
    assert [i["delivered_at"] for i in items] == [None, None]  # no state change from delivery
    assert order_id not in delivery._in_flight  # the guard is free
    assert any("stopped mid-flight" in r.getMessage() for r in caplog.records)
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)
    assert first["id"] != second["id"]
    # A later delivery attempt for the refunded order does nothing at all.
    again = fake_bot()
    assert await delivery.deliver_order(again, order_id) == ([], [])
    again.send_message.assert_not_called()


async def test_an_order_refunded_before_the_next_item_sends_nothing_more(db):
    order_id = paid_order({TEXT: 1, 2: 1})
    bot = fake_bot()
    sent = []

    async def send_then_refund(**kwargs):
        sent.append(kwargs["text"])
        if len(sent) == 1:
            orders_repo.mark_refunded(order_id, "owner")  # lands between the two items

    bot.send_message.side_effect = send_then_refund
    result = await delivery.deliver_order(bot, order_id)

    assert len(sent) == 1 and result == ([], [])
    assert orders_repo.get(order_id)["status"] == "refunded"
    assert order_id not in delivery._in_flight


async def test_illegal_transition_on_the_final_move_is_caught_quietly(db, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    order_id = paid_order({TEXT: 1})

    def lost_the_race(oid):
        raise orders_repo.IllegalTransitionError("refunded", "delivered")

    monkeypatch.setattr(orders_repo, "finish_order_if_delivered", lost_the_race)
    bot = fake_bot()
    result = await delivery.deliver_order(bot, order_id)

    assert len(result.delivered) == 1 and result.failed == []  # the item itself was sent
    assert len(_texts(bot)) == 1  # but no receipt for an order that did not complete
    assert order_id not in delivery._in_flight
    assert any("left 'paid'" in r.getMessage() for r in caplog.records)
    assert not any(r.levelno >= logging.WARNING for r in caplog.records)
