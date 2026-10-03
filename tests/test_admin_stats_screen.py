"""The Stats screen and the dashboard's attention count."""

from __future__ import annotations

import re

import pytest

from bot.handlers import admin_catalogue
from bot.repo import orders as orders_repo

from .conftest import OWNER_ID
from .flow import Flow
from .helpers import add_customer, make_message, make_update
from .shop import KEY, TEXT, pay, place_order

HOSTILE = "<b>&"


@pytest.fixture
def flow(db, monkeypatch):
    add_customer(OWNER_ID)
    return Flow(monkeypatch, OWNER_ID)


def sale(user: int, lines, mode: str = "stars", delivered: bool = True) -> int:
    order_id = place_order(user, lines, mode=mode)
    pay(order_id)
    if delivered:
        for item in orders_repo.list_undelivered_items(order_id):
            orders_repo.mark_item_delivered(item["id"])
        orders_repo.finish_order_if_delivered(order_id)
    return order_id


async def test_an_empty_shop_renders_all_zeros(flow):
    await flow.tap(admin_catalogue.route, "adm:st")
    text = flow.text
    assert text.startswith("<b>Stats</b>")
    assert "<b>Real money (Telegram Stars)</b>" in text
    assert "<b>Demo (mock) — not real money</b>" in text
    assert text.count("Last 7 days: gross ⭐ 0 · refunded ⭐ 0 · net ⭐ 0") == 2
    assert text.count("(no sales yet)") == 2
    assert "pending payment: 0" in text and "refunded: 0" in text
    assert flow.labels() == ["Back to admin"]


async def test_real_and_demo_sections_are_separate_with_their_own_top_editions(flow):
    sale(3101, {TEXT: 1}, mode="stars")  # ⭐ 75
    sale(3102, {KEY: 2}, mode="mock")  # ⭐ 100 demo
    refunded = sale(3103, {TEXT: 1}, mode="stars")
    orders_repo.mark_refunded(refunded, "owner")

    await flow.tap(admin_catalogue.route, "adm:st")
    real, demo = flow.text.split("<b>Demo (mock) — not real money</b>")

    assert "Last 7 days: gross ⭐ 150 · refunded ⭐ 75 · net ⭐ 75" in real
    assert "All time: gross ⭐ 150 · refunded ⭐ 75 · net ⭐ 75" in real
    assert "1. Neon Portrait Prompt Pack - Personal — 1" in real
    assert "Orbit Icons Pro" not in real  # the demo sale does not leak into the real list

    assert "Last 7 days: gross ⭐ 100 · refunded ⭐ 0 · net ⭐ 100" in demo
    assert "1. Orbit Icons Pro - Personal — 2" in demo
    assert "Neon Portrait" not in demo.split("Orders by status")[0]
    assert "delivered: 2" in demo and "refunded: 1" in demo
    assert "Customers: 4 total" in demo  # the owner plus three buyers


async def test_hostile_edition_names_render_escaped_on_the_stats_screen(flow, db):
    sale(3101, {TEXT: 1})
    db.execute("UPDATE order_items SET product_name = ?, variant_label = ?", (HOSTILE, HOSTILE))
    db.commit()
    await flow.tap(admin_catalogue.route, "adm:st")
    assert "1. &lt;b&gt;&amp; - &lt;b&gt;&amp; — 1" in flow.text
    assert "&" not in re.sub(r"&(?:amp|lt|gt);", "", flow.text)
    assert set(re.findall(r"<[^>]*>", flow.text)) <= {"<b>", "</b>"}


async def test_a_negative_net_renders(flow, db):
    order_id = sale(3101, {TEXT: 1})
    db.execute("UPDATE orders SET paid_at = datetime('now', '-30 days')")
    db.commit()
    orders_repo.mark_refunded(order_id, "owner")
    await flow.tap(admin_catalogue.route, "adm:st")
    assert "Last 7 days: gross ⭐ 0 · refunded ⭐ 75 · net ⭐ -75" in flow.text
    assert "All time: gross ⭐ 75 · refunded ⭐ 75 · net ⭐ 0" in flow.text


# --- the dashboard ----------------------------------------------------------------------------


async def open_dashboard(db, monkeypatch):
    add_customer(OWNER_ID)
    flow = Flow(monkeypatch, OWNER_ID)
    update = make_update(OWNER_ID, message=make_message("/admin"), chat_id=OWNER_ID)
    await admin_catalogue.dashboard(update, flow.context)
    call = flow.sent_messages()[-1]
    labels = [b.text for row in call.kwargs["reply_markup"].inline_keyboard for b in row]
    return str(call.kwargs["text"]), labels


async def test_the_dashboard_without_waiting_deliveries(db, monkeypatch):
    text, labels = await open_dashboard(db, monkeypatch)
    assert labels == ["Catalogue", "Orders", "Stats", "Settings"]
    assert "awaiting delivery" not in text


async def test_the_dashboard_shows_the_attention_count_and_line(db, monkeypatch):
    sale(3101, {TEXT: 1}, delivered=False)
    text, labels = await open_dashboard(db, monkeypatch)
    assert labels == ["Catalogue", "Orders (1 need attention)", "Stats", "Settings"]
    assert "1 paid order awaiting delivery" in text

    sale(3102, {KEY: 1}, delivered=False)
    text, labels = await open_dashboard(db, monkeypatch)
    assert "Orders (2 need attention)" in labels and "2 paid orders awaiting delivery" in text


async def test_delivered_and_pending_orders_do_not_count_as_attention(db, monkeypatch):
    sale(3101, {TEXT: 1}, delivered=True)
    place_order(3102, {TEXT: 1})
    text, labels = await open_dashboard(db, monkeypatch)
    assert "Orders" in labels and "need attention" not in " ".join(labels)
    assert "awaiting delivery" not in text
