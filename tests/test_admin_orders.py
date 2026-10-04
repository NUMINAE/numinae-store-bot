"""Admin order management through the real handlers."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock

import pytest
from telegram.error import Forbidden

from bot import jobs
from bot.handlers import admin_catalogue, admin_orders, catalogue, checkout, library
from bot.handlers.admin_orders import age
from bot.refunds import RefundResult, RefundStatus
from bot.repo import cart as cart_repo
from bot.repo import customers as customers_repo
from bot.repo import orders as orders_repo
from bot.ui_kit import ICON

from .conftest import OWNER_ID
from .flow import Flow
from .helpers import HOME, add_customer, make_context, make_message, make_update
from .shop import KEY, TEXT, key_states, pay, place_order

BUYER = 3001
OTHER = 3002
HOSTILE = "<b>&"


@pytest.fixture
def flow(db, monkeypatch):
    add_customer(OWNER_ID)
    return Flow(monkeypatch, OWNER_ID)


@pytest.fixture(autouse=True)
def clean_sweep_state():
    jobs._retry.clear()
    yield
    jobs._retry.clear()


def card_buttons(flow: Flow) -> list[str]:
    return [label for label, _ in flow.buttons]


def to_buyer(flow: Flow, user_id: int = BUYER) -> list[str]:
    return [str(c.kwargs["text"]) for c in flow.sent_messages() if c.kwargs["chat_id"] == user_id]


def delivered_order(lines=None, user=BUYER, mode="stars") -> int:
    order_id = place_order(user, lines or {TEXT: 1}, mode=mode)
    pay(order_id)
    for item in orders_repo.list_undelivered_items(order_id):
        orders_repo.mark_item_delivered(item["id"])
    orders_repo.finish_order_if_delivered(order_id)
    return order_id


def paid_order(lines=None, user=BUYER, mode="stars") -> int:
    order_id = place_order(user, lines or {TEXT: 1}, mode=mode)
    pay(order_id)
    return order_id


# --- browsing ---------------------------------------------------------------------------------


async def test_the_root_shows_every_filter_with_live_counts(flow):
    place_order(3101)  # pending
    paid_order(user=3102)
    delivered_order(user=3103)
    await flow.tap(admin_catalogue.route, "adm:o")
    assert flow.labels() == [
        "Needs attention (1)",
        "All (3)",
        "Pending payment (1)",
        "Paid (1)",
        "Delivered (1)",
        "Refunded (0)",
        "Closed (0)",
        "‹ Back",
        HOME,
    ]
    assert flow.buttons[1][1] == "adm:o:l:all:0"


async def test_the_list_shows_id_stars_status_and_age_newest_first(flow, db):
    first = paid_order(user=3101)
    second = delivered_order({KEY: 2}, user=3102)
    db.execute("UPDATE orders SET created_at = datetime('now', '-3 hours') WHERE id = ?", (first,))
    db.commit()
    await flow.tap(admin_catalogue.route, "adm:o:l:all:0")
    assert flow.labels()[:2] == [
        f"{ICON.delivered} #{second} · ⭐ 100 · Delivered · 0m",
        f"{ICON.paid} #{first} · ⭐ 75 · Paid · 3h",
    ]
    assert flow.buttons[0][1] == f"adm:o:v:{second}"


async def test_each_filter_lists_only_its_orders(flow):
    pending = place_order(3101)
    paid = paid_order(user=3102)
    refunded = delivered_order(user=3103)
    orders_repo.mark_refunded(refunded, "owner")
    for filter_key, expected in (
        ("pending_payment", pending),
        ("paid", paid),
        ("attention", paid),
        ("refunded", refunded),
    ):
        await flow.tap(admin_catalogue.route, f"adm:o:l:{filter_key}:0")
        assert [d for _, d in flow.buttons if d.startswith("adm:o:v:")] == [f"adm:o:v:{expected}"]


async def test_paging_is_five_per_page_with_arrows_omitted_at_the_edges(flow):
    for n in range(7):
        paid_order(user=3200 + n)
    await flow.tap(admin_catalogue.route, "adm:o:l:all:0")
    assert len([d for _, d in flow.buttons if d.startswith("adm:o:v:")]) == 5
    assert "◀" not in flow.labels() and "▶" in flow.labels() and "1 / 2" in flow.labels()
    await flow.tap(admin_catalogue.route, "adm:o:l:all:1")
    assert len([d for _, d in flow.buttons if d.startswith("adm:o:v:")]) == 2
    assert "◀" in flow.labels() and "▶" not in flow.labels()
    await flow.tap(admin_catalogue.route, "adm:o:l:all:99")  # clamped
    assert "2 / 2" in flow.labels()


async def test_an_empty_filter_and_an_unknown_filter(flow):
    await flow.tap(admin_catalogue.route, "adm:o:l:refunded:0")
    assert "No orders here." in flow.text and flow.data_for("‹ Back") == "adm:o"
    await flow.tap(admin_catalogue.route, "adm:o:l:bogus:0")  # falls back to the root
    assert "All (0)" in flow.labels()


def test_age_formatting():
    from datetime import UTC, datetime, timedelta

    def stamp(**delta):
        return (datetime.now(UTC) - timedelta(**delta)).strftime("%Y-%m-%d %H:%M:%S")

    assert age(stamp(seconds=5)) == "0m"
    assert age(stamp(minutes=7, seconds=5)) == "7m"
    assert age(stamp(hours=5, minutes=1)) == "5h"
    assert age(stamp(days=3, hours=2)) == "3d"
    assert age("garbage") == "?" and age(None) == "?"


# --- the card ---------------------------------------------------------------------------------


async def test_the_card_shows_everything_the_owner_needs(flow, db):
    order_id = delivered_order({TEXT: 1, KEY: 2})
    customers_repo.upsert(BUYER, "alice_buyer", "Alice")
    await flow.tap(admin_catalogue.route, f"adm:o:v:{order_id}")

    text = flow.text
    assert f"<b>Order #{order_id}</b>" in text and f"{ICON.delivered} Delivered" in text
    assert "Buyer: Alice @alice_buyer (<code>3001</code>)" in text
    for label in ("Created:", "Paid:", "Delivered:", "Expires:"):
        assert label in text
    assert "Refunded:" not in text
    assert "Total: ⭐ 175 · Mode: stars" in text
    assert f"Charge id: <code>ch_{order_id}</code>" in text
    assert "Neon Portrait Prompt Pack — Personal × 1 — ⭐ 75 — text, delivered 20" in text
    assert "Orbit Icons Pro — Personal × 2 — ⭐ 100 — key, delivered 20" in text
    assert "pending_payment → paid [system] — Payment received" in text
    assert "paid → delivered [system]" in text


async def test_the_card_lists_only_the_last_five_events(flow):
    order_id = paid_order()
    for n in range(6):
        orders_repo.add_note(order_id, "system", f"note number {n}")
    await flow.tap(admin_catalogue.route, f"adm:o:v:{order_id}")
    assert "note number 0" not in flow.text and "note number 5" in flow.text
    assert flow.text.count("[system]") == 5


async def test_an_undelivered_item_says_not_delivered(flow):
    order_id = paid_order({TEXT: 1})
    await flow.tap(admin_catalogue.route, f"adm:o:v:{order_id}")
    assert "text, not delivered" in flow.text


async def test_hostile_names_render_literally_on_the_card(flow, db):
    order_id = paid_order({TEXT: 1})
    customers_repo.upsert(BUYER, HOSTILE, HOSTILE)
    db.execute("UPDATE order_items SET product_name = ?, variant_label = ?", (HOSTILE, HOSTILE))
    db.commit()
    orders_repo.add_note(order_id, HOSTILE, HOSTILE)
    await flow.tap(admin_catalogue.route, f"adm:o:v:{order_id}")
    text = flow.text
    escaped = "&lt;b&gt;&amp;"
    assert f"Buyer: {escaped} @{escaped} (<code>3001</code>)" in text
    assert f"• {escaped} — {escaped} × 1" in text
    assert f"[{escaped}] — {escaped}" in text
    import re

    assert "&" not in re.sub(r"&(?:amp|lt|gt);", "", text)
    assert set(re.findall(r"<[^>]*>", text)) <= {"<b>", "</b>", "<code>", "</code>"}


async def test_a_buyer_without_a_name_or_username(flow, db):
    order_id = paid_order()
    db.execute(
        "UPDATE customers SET first_name = NULL, username = NULL WHERE user_id = ?", (BUYER,)
    )
    db.commit()
    await flow.tap(admin_catalogue.route, f"adm:o:v:{order_id}")
    assert "Buyer: (no name) (<code>3001</code>)" in flow.text


async def test_a_missing_order_card(flow):
    await flow.tap(admin_catalogue.route, "adm:o:v:99999")
    assert flow.alert == "Order not found."


@pytest.mark.parametrize(
    ("make", "expected"),
    [
        (lambda: place_order(BUYER), ["Cancel order"]),
        (lambda: paid_order(), ["Retry delivery", "Refund"]),
        (lambda: delivered_order(), ["Resend to buyer", "Refund"]),
    ],
)
async def test_actions_offered_by_status(flow, make, expected):
    order_id = make()
    await flow.tap(admin_catalogue.route, f"adm:o:v:{order_id}")
    assert card_buttons(flow) == [*expected, "‹ Back", HOME]


@pytest.mark.parametrize("final", ["refunded", "cancelled", "expired"])
async def test_closed_orders_offer_no_actions(flow, final):
    order_id = place_order(BUYER)
    if final == "cancelled":
        orders_repo.cancel_order(order_id, "customer")
    elif final == "expired":
        orders_repo.set_status(order_id, "expired", "system")
    else:
        pay(order_id)
        orders_repo.mark_refunded(order_id, "owner")
    await flow.tap(admin_catalogue.route, f"adm:o:v:{order_id}")
    assert card_buttons(flow) == ["‹ Back", HOME]


# --- cancel -----------------------------------------------------------------------------------


async def test_cancel_confirms_then_releases_keys_without_restoring_the_cart_and_notifies(flow, db):
    order_id = place_order(BUYER, {TEXT: 1, KEY: 3})
    await flow.tap(admin_catalogue.route, f"adm:ord:cancel:{order_id}")
    assert "Cancel order" in flow.text and "NOT restored" in flow.text
    assert orders_repo.get(order_id)["status"] == "pending_payment"  # nothing yet

    await flow.tap(admin_catalogue.route, f"adm:ord:cancel:{order_id}:yes")

    assert orders_repo.get(order_id)["status"] == "cancelled"
    assert key_states(db) == {"available": 10}
    assert cart_repo.list_lines(BUYER) == []  # the owner's cancel never restores the cart
    assert "Order cancelled." in flow.text and "Could not notify" not in flow.text
    assert to_buyer(flow) == [f"Your order #{order_id} was cancelled by the shop."]
    event = orders_repo.list_events(order_id)[-1]
    assert (event["to_status"], event["actor"]) == ("cancelled", "owner")


async def test_a_failed_buyer_notification_is_shown_on_the_card(flow, caplog):
    caplog.set_level(logging.ERROR)
    order_id = place_order(BUYER)

    async def send(**kwargs):
        if kwargs["chat_id"] == BUYER:
            raise Forbidden("bot was blocked")

    flow.context.bot.send_message.side_effect = send
    await flow.tap(admin_catalogue.route, f"adm:ord:cancel:{order_id}:yes")
    assert orders_repo.get(order_id)["status"] == "cancelled"  # the action still happened
    assert "Could not notify customer." in flow.text
    assert any(r.levelno == logging.ERROR for r in caplog.records)


# --- retry delivery and resend ----------------------------------------------------------------


async def test_retry_delivery_delivers_a_stuck_order_and_reports(flow, db):
    order_id = paid_order({TEXT: 1, KEY: 1})
    await flow.tap(admin_catalogue.route, f"adm:ord:retry:{order_id}")
    assert "Delivered 2 items." in flow.text
    assert orders_repo.get(order_id)["status"] == "delivered"
    assert any("example.com/demo" in m for m in to_buyer(flow))


async def test_retry_delivery_reports_items_that_still_fail_and_resets_the_backoff(flow):
    order_id = paid_order({TEXT: 1})
    jobs._retry[order_id] = jobs._Retry(5, 10**9)  # deep in backoff
    flow.context.bot.send_message.side_effect = Forbidden("blocked")
    await flow.tap(admin_catalogue.route, f"adm:ord:retry:{order_id}")
    assert "1 still failing" in flow.text and "keep retrying" in flow.text
    assert jobs._retry[order_id].failures == 1  # reset, then this failure counted afresh
    assert orders_repo.get(order_id)["status"] == "paid"


async def test_retry_with_nothing_to_send_says_so(flow, monkeypatch):
    order_id = paid_order({TEXT: 1})

    async def nothing(bot, oid):
        from bot.delivery import DeliveryResult

        return DeliveryResult([], [])

    monkeypatch.setattr(jobs.delivery, "deliver_order", nothing)
    await flow.tap(admin_catalogue.route, f"adm:ord:retry:{order_id}")
    assert "Nothing was sent" in flow.text


async def test_resend_sends_every_item_again_without_touching_state(flow):
    order_id = delivered_order({TEXT: 1, KEY: 2})
    items_before = [dict(i) for i in orders_repo.get_with_items(order_id)[1]]
    order_before = dict(orders_repo.get(order_id))
    events_before = len(orders_repo.list_events(order_id))

    await flow.tap(admin_catalogue.route, f"adm:ord:resend:{order_id}")

    assert "Re-sent 2 of 2 items to the buyer." in flow.text
    assert len(to_buyer(flow)) == 2
    assert [dict(i) for i in orders_repo.get_with_items(order_id)[1]] == items_before
    assert dict(orders_repo.get(order_id)) == order_before
    assert len(orders_repo.list_events(order_id)) == events_before


async def test_resend_counts_the_items_that_failed(flow):
    order_id = delivered_order({TEXT: 1, 2: 1})
    flow.context.bot.send_message.side_effect = [None, Forbidden("blocked")]
    await flow.tap(admin_catalogue.route, f"adm:ord:resend:{order_id}")
    assert "Re-sent 1 of 2 items" in flow.text


# --- refunds ----------------------------------------------------------------------------------


async def test_refund_confirmation_states_the_amount_revocation_and_what_cannot_be_unsent(flow):
    order_id = delivered_order({KEY: 2})
    await flow.tap(admin_catalogue.route, f"adm:ord:refund:{order_id}")
    assert "full ⭐ 100 goes back to the buyer" in flow.text
    assert "Sold licence keys will be revoked" in flow.text
    assert "Delivered text and files cannot be unsent" in flow.text
    assert flow.labels() == ["Confirm", "Cancel", "‹ Back", HOME]
    assert orders_repo.get(order_id)["status"] == "delivered"  # nothing refunded yet
    flow.context.bot.refund_star_payment.assert_not_called()


async def test_a_confirmed_refund_refunds_revokes_notifies_and_rerenders(flow, db):
    order_id = delivered_order({KEY: 3})
    flow.context.bot.refund_star_payment.return_value = True

    await flow.tap(admin_catalogue.route, f"adm:ord:refund:{order_id}:yes")

    flow.context.bot.refund_star_payment.assert_awaited_once_with(
        user_id=BUYER, telegram_payment_charge_id=f"ch_{order_id}"
    )
    assert orders_repo.get(order_id)["status"] == "refunded"
    assert key_states(db) == {"available": 7, "revoked": 3}
    assert "Refunded: 3 keys revoked." in flow.text and "— Refunded" in flow.text
    assert card_buttons(flow) == ["‹ Back", HOME]
    (notice,) = to_buyer(flow)
    assert notice.startswith(f"Your order #{order_id} was refunded (⭐ 150).")
    assert "licence keys from this order are no longer valid" in notice


async def test_a_refund_for_a_mock_order_never_reaches_telegram(flow):
    order_id = delivered_order({TEXT: 1}, mode="mock")
    await flow.tap(admin_catalogue.route, f"adm:ord:refund:{order_id}:yes")
    assert orders_repo.get(order_id)["status"] == "refunded"
    flow.context.bot.refund_star_payment.assert_not_called()


async def test_provider_failure_shows_the_escaped_detail_and_changes_nothing(flow, db, monkeypatch):
    order_id = delivered_order({KEY: 2})
    monkeypatch.setattr(
        admin_orders,
        "refund_order",
        AsyncMock(return_value=RefundResult(RefundStatus.PROVIDER_FAILED, HOSTILE)),
    )
    await flow.tap(admin_catalogue.route, f"adm:ord:refund:{order_id}:yes")
    assert "Refund failed: &lt;b&gt;&amp;" in flow.text and "<b>&" not in flow.text
    assert orders_repo.get(order_id)["status"] == "delivered"
    assert to_buyer(flow) == []  # the buyer is only told about a refund that happened


async def test_a_real_provider_failure_flows_through_end_to_end(flow, db):
    order_id = delivered_order({KEY: 2})
    flow.context.bot.refund_star_payment.return_value = False
    await flow.tap(admin_catalogue.route, f"adm:ord:refund:{order_id}:yes")
    assert "Refund failed:" in flow.text
    assert orders_repo.get(order_id)["status"] == "delivered"
    assert key_states(db)["sold"] == 2


async def test_a_second_refund_answers_already_refunded(flow):
    order_id = delivered_order({TEXT: 1})
    await flow.tap(admin_catalogue.route, f"adm:ord:refund:{order_id}:yes")
    await flow.tap(admin_catalogue.route, f"adm:ord:refund:{order_id}:yes")  # stale confirm tap
    assert flow.alert == "Already refunded."
    assert "— Refunded" in flow.text
    assert flow.context.bot.refund_star_payment.await_count == 1


async def test_a_double_tapped_refund_calls_the_provider_once(flow):
    order_id = delivered_order({KEY: 2})
    gate = asyncio.Event()
    entered = asyncio.Event()

    async def slow_refund(**kwargs):
        entered.set()
        await gate.wait()
        return True

    flow.context.bot.refund_star_payment.side_effect = slow_refund
    second_flow_edit = flow.edit  # both taps share one fake chat
    first = asyncio.ensure_future(flow.tap(admin_catalogue.route, f"adm:ord:refund:{order_id}:yes"))
    await asyncio.wait_for(entered.wait(), timeout=2)
    await flow.tap(admin_catalogue.route, f"adm:ord:refund:{order_id}:yes")  # the double tap

    assert "already in progress" in flow.text
    assert flow.context.bot.refund_star_payment.await_count == 1
    gate.set()
    await first
    assert flow.context.bot.refund_star_payment.await_count == 1
    assert orders_repo.get(order_id)["status"] == "refunded"
    assert second_flow_edit is flow.edit


async def test_no_charge_id_is_explained(flow):
    order_id = place_order(BUYER, {TEXT: 1}, mode="stars")
    orders_repo.set_status(order_id, "paid", "system")  # paid but no charge id
    await flow.tap(admin_catalogue.route, f"adm:ord:refund:{order_id}:yes")
    assert "No payment charge id is stored" in flow.text
    flow.context.bot.refund_star_payment.assert_not_called()


async def test_a_database_failure_after_the_refund_alerts_the_owner_loudly(flow, monkeypatch):
    order_id = delivered_order({KEY: 2})
    monkeypatch.setattr(
        orders_repo, "mark_refunded", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk"))
    )
    await flow.tap(admin_catalogue.route, f"adm:ord:refund:{order_id}:yes")

    assert "Money was refunded but the status was not updated" in flow.text
    alerts = to_buyer(flow, OWNER_ID)
    assert len(alerts) == 1
    assert f"Order: #{order_id}" in alerts[0] and f"<code>ch_{order_id}</code>" in alerts[0]
    assert "Money was refunded, status was not updated, reconcile manually." in alerts[0]
    assert to_buyer(flow) == []  # and the buyer is not told about a refund we could not record
    assert orders_repo.get(order_id)["status"] == "delivered"


async def test_refund_of_an_order_that_is_not_refundable_reports_its_real_status(flow):
    order_id = place_order(BUYER)  # still pending
    await flow.tap(admin_catalogue.route, f"adm:ord:refund:{order_id}:yes")
    assert flow.alert == f"Order #{order_id} is now: Pending payment."
    flow.context.bot.refund_star_payment.assert_not_called()


# --- idempotency ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("action", "status_word", "make"),
    [
        ("cancel:{id}:yes", "Paid", lambda: paid_order()),
        ("cancel:{id}", "Delivered", lambda: delivered_order()),
        ("retry:{id}", "Delivered", lambda: delivered_order()),
        ("resend:{id}", "Paid", lambda: paid_order()),
        ("refund:{id}", "Cancelled", lambda: _cancelled()),
        ("retry:{id}", "Cancelled", lambda: _cancelled()),
    ],
)
async def test_stale_actions_answer_with_the_real_status_and_rerender_the_card(
    flow, action, status_word, make
):
    order_id = make()
    before = (dict(orders_repo.get(order_id)), len(orders_repo.list_events(order_id)))
    await flow.tap(admin_catalogue.route, "adm:ord:" + action.format(id=order_id))
    assert flow.alert == f"Order #{order_id} is now: {status_word}."
    assert (
        f"<b>Order #{order_id}</b>" in flow.text and f" {status_word}\n" in flow.text
    )  # the card, re-rendered
    assert (dict(orders_repo.get(order_id)), len(orders_repo.list_events(order_id))) == before
    assert to_buyer(flow) == []


def _cancelled() -> int:
    order_id = place_order(BUYER)
    orders_repo.cancel_order(order_id, "customer")
    return order_id


async def test_an_illegal_transition_race_answers_with_the_real_status(flow, monkeypatch):
    order_id = place_order(BUYER)
    real = orders_repo.cancel_order

    def lose_the_race(oid, actor):
        orders_repo.set_status(oid, "expired", "system")  # the sweep got there first
        return real(oid, actor)

    monkeypatch.setattr(orders_repo, "cancel_order", lose_the_race)
    await flow.tap(admin_catalogue.route, f"adm:ord:cancel:{order_id}:yes")
    assert flow.alert == f"Order #{order_id} is now: Expired."
    assert to_buyer(flow) == []


async def test_actions_on_a_missing_order(flow):
    for action in ("cancel", "retry", "resend", "refund"):
        await flow.tap(admin_catalogue.route, f"adm:ord:{action}:99999")
        assert flow.alert == "Order not found."


# --- /order -----------------------------------------------------------------------------------


def command(user_id: int, *args: str):
    message = make_message("/order " + " ".join(args))
    update = make_update(user_id, message=message, chat_id=user_id)
    context = make_context()
    context.args = list(args)
    return update, context, message


async def test_order_command_opens_the_card_for_the_owner(db):
    add_customer(OWNER_ID)
    order_id = paid_order()
    update, context, message = command(OWNER_ID, str(order_id))
    await admin_orders.find_order(update, context)
    text = str(context.bot.send_message.await_args.kwargs["text"])
    assert f"<b>Order #{order_id}</b>" in text and f"{ICON.paid} Paid" in text
    message.reply_text.assert_not_called()


async def test_order_command_for_a_missing_order(db):
    update, context, message = command(OWNER_ID, "424242")
    await admin_orders.find_order(update, context)
    assert message.reply_text.await_args.args[0] == "No order #424242."


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["abc"],
        ["-1"],
        ["-5"],
        ["1.5"],
        ["0x10"],
        ["1e3"],
        [""],
        ["9223372036854775808"],  # 2**63: one above the SQLite maximum
        ["99999999999999999999999999"],
        ["1", "2"],
        ["12abc"],
        ["--help"],
    ],
)
async def test_order_command_malformed_arguments_get_the_usage_message_and_never_raise(db, args):
    update, context, message = command(OWNER_ID, *args)
    await admin_orders.find_order(update, context)
    assert "Usage: /order <order id>" in message.reply_text.await_args.args[0]
    context.bot.send_message.assert_not_called()


async def test_order_command_accepts_the_largest_valid_id(db):
    update, context, message = command(OWNER_ID, str(2**63 - 1))
    await admin_orders.find_order(update, context)
    assert message.reply_text.await_args.args[0] == f"No order #{2**63 - 1}."


async def test_order_command_with_no_args_attribute_at_all(db):
    message = make_message("/order")
    update = make_update(OWNER_ID, message=message, chat_id=OWNER_ID)
    context = make_context()
    context.args = None  # PTB leaves args as None outside a command handler
    await admin_orders.find_order(update, context)
    assert "Usage:" in message.reply_text.await_args.args[0]


async def test_order_command_refuses_non_owners_and_reveals_nothing(db):
    order_id = paid_order()
    add_customer(OTHER)
    update, context, message = command(OTHER, str(order_id))
    await admin_orders.find_order(update, context)
    assert message.reply_text.await_args.args[0] == "You are not authorized to do that."
    context.bot.send_message.assert_not_called()
    update, context, message = command(OTHER, "garbage")  # even a malformed one: same refusal
    await admin_orders.find_order(update, context)
    assert message.reply_text.await_args.args[0] == "You are not authorized to do that."


# --- the whole journey ------------------------------------------------------------------------


async def test_buy_a_key_edition_in_mock_mode_then_refund_it_from_the_admin_card(db, monkeypatch):
    add_customer(BUYER)
    buyer = Flow(monkeypatch, BUYER, mode="mock")
    await buyer.tap(catalogue.route, f"cat:add:{KEY}:0:3")
    await buyer.tap(checkout.route, "co:pay")
    order_id = orders_repo.get_open_for_user(BUYER)["id"]
    await buyer.tap(checkout.route, f"co:mockpay:{order_id}")
    await buyer.drain()
    assert orders_repo.get(order_id)["status"] == "delivered"
    assert key_states(db) == {"available": 7, "sold": 3}

    add_customer(OWNER_ID)
    owner = Flow(monkeypatch, OWNER_ID, mode="mock")
    await owner.tap(admin_catalogue.route, f"adm:o:v:{order_id}")
    assert "Resend to buyer" in owner.labels() and "Refund" in owner.labels()
    await owner.tap(admin_catalogue.route, f"adm:ord:refund:{order_id}")
    await owner.tap(admin_catalogue.route, f"adm:ord:refund:{order_id}:yes")
    assert "Refunded: 3 keys revoked." in owner.text

    assert orders_repo.get(order_id)["status"] == "refunded"
    assert key_states(db) == {"available": 7, "revoked": 3}  # revoked, never available again
    owner.context.bot.refund_star_payment.assert_not_called()  # a mock order stays off Telegram
    assert any("was refunded" in m for m in to_buyer(owner))

    buyer_again = Flow(monkeypatch, BUYER, mode="mock")
    await buyer_again.tap(library.route, "lib:list:0")
    assert buyer_again.labels()[0].endswith("· Refunded")
    item_id = int(buyer_again.buttons[0][1].rsplit(":", 1)[1])
    await buyer_again.tap(library.route, f"lib:item:{item_id}")
    assert "Status: ↩️ Refunded" in buyer_again.text
    assert not any(d.startswith("lib:send:") for _, d in buyer_again.buttons)

    owner_again = Flow(monkeypatch, OWNER_ID, mode="mock")
    await owner_again.tap(admin_catalogue.route, f"adm:ord:refund:{order_id}:yes")
    assert owner_again.alert == "Already refunded."
