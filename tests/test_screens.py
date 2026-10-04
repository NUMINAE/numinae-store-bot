"""Whole-bot screen tests: the crawler walks every reachable screen, the structural
checks run on each one, and a hostile catalogue (every name made of "&", maximum
descriptions, the most editions) must still render under Telegram's limit with
nothing double-escaped."""

from __future__ import annotations

import html

from bot.handlers import checkout
from bot.repo import categories as categories_repo
from bot.repo import license_keys as keys_repo
from bot.repo import orders as orders_repo
from bot.repo import products as products_repo
from bot.repo import settings as settings_repo
from bot.repo import variants as variants_repo
from bot.ui_kit import ICON

from .conftest import OWNER_ID
from .flow import Flow
from .helpers import add_customer
from .screens import (
    CRAWL_BOUND,
    TELEGRAM_TEXT_LIMIT,
    crawl,
    markup_problems,
    screen_problems,
    tap,
    unescaped,
)
from .shop import KEY, TEXT, fill_cart, pay, place_order

USER = 4001
NL = chr(10)
HOSTILE = "&" * 60
HOSTILE_LONG = "&" * 700
MIN_SCREENS = 40  # the demo catalogue alone reaches far more than this

# The most any screen is asked to carry in these tests.
EDITIONS = 60
CART_LINES = 12


def _deliver(order_id: int) -> None:
    for item in orders_repo.list_undelivered_items(order_id):
        orders_repo.mark_item_delivered(item["id"])
    orders_repo.finish_order_if_delivered(order_id)


def _lived_in_shop(db) -> None:
    """Orders in every state plus a library, so every screen has something to show."""
    add_customer(USER)
    bought = place_order(USER, {TEXT: 1, KEY: 2}, mode="mock")
    pay(bought)
    _deliver(bought)
    paid_only = place_order(USER, {TEXT: 1}, mode="mock")
    pay(paid_only)
    orders_repo.set_status(paid_only, "refunded", "owner")
    place_order(USER, {2: 1}, mode="mock")  # left pending
    fill_cart(USER, {TEXT: 1, KEY: 2})


def _hostile_shop(db) -> int:
    """Every length at its limit, every character one that needs escaping."""
    category = categories_repo.create(HOSTILE)
    categories_repo.update_description(category, HOSTILE_LONG)
    product = products_repo.create(category, HOSTILE)
    products_repo.update_description(product, HOSTILE_LONG)
    editions = []
    for n in range(EDITIONS):
        kind = "key" if n % 3 == 0 else "text"
        variant = variants_repo.create(product, f"{n:02d}{HOSTILE[:58]}", 10 + n, kind)
        if kind == "key":
            keys_repo.add_many(variant, [f"{n:02d}{i:03d}" + "&" * 190 for i in range(12)])
        else:
            variants_repo.set_delivery_text(variant, "&" * 2000)
        editions.append(variant)
    settings_repo.set("welcome_text", "&" * 1000)
    settings_repo.set("support_note", "&" * 1000)

    add_customer(USER)
    paid = place_order(USER, {v: 1 for v in editions[:CART_LINES]}, mode="mock")
    pay(paid)
    _deliver(paid)
    orders_repo.add_note(paid, "&" * 60, "&" * 200)
    for _ in range(5):
        orders_repo.add_note(paid, "&" * 20, "&" * 200)
    fill_cart(USER, {v: 1 for v in editions[:CART_LINES]})
    # A hostile pending order too, for the checkout "resume" screen.
    place_order(OWNER_ID + 1000, {editions[1]: 1}, mode="mock")
    return product


async def _crawl_as(monkeypatch, user_id: int, start: str):
    flow = Flow(monkeypatch, user_id, mode="mock")
    result = await crawl(flow, start)
    await flow.drain()
    return flow, result


# --- the crawler ------------------------------------------------------------------------------


async def test_the_customer_crawler_finds_no_dead_ends(db, monkeypatch):
    _lived_in_shop(db)
    _, result = await _crawl_as(monkeypatch, USER, "menu:show")
    print(f"customer crawl: {result.count} screens, {result.taps} taps")
    assert not result.problems, NL.join(result.problems)
    assert result.exhausted and MIN_SCREENS <= result.count < CRAWL_BOUND


async def test_the_owner_crawler_finds_no_dead_ends(db, monkeypatch):
    _lived_in_shop(db)
    _, result = await _crawl_as(monkeypatch, OWNER_ID, "adm:home")
    print(f"owner crawl: {result.count} screens, {result.taps} taps")
    assert not result.problems, NL.join(result.problems)
    assert result.exhausted and MIN_SCREENS <= result.count < CRAWL_BOUND


async def test_the_crawler_under_hostile_content_stays_under_the_limit(db, monkeypatch):
    _hostile_shop(db)
    flow, result = await _crawl_as(monkeypatch, USER, "menu:show")
    print(f"hostile customer crawl: {result.count} screens, {result.taps} taps")
    assert not result.problems, NL.join(result.problems)
    assert result.exhausted and MIN_SCREENS <= result.count < CRAWL_BOUND
    longest = max(len(text) for _, text in result.screens)
    assert longest < TELEGRAM_TEXT_LIMIT
    assert flow.context.bot.send_message.await_count > 0  # deliveries and summaries were checked


async def test_the_owner_crawler_under_hostile_content_stays_under_the_limit(db, monkeypatch):
    _hostile_shop(db)
    _, result = await _crawl_as(monkeypatch, OWNER_ID, "adm:home")
    print(f"hostile owner crawl: {result.count} screens, {result.taps} taps")
    assert not result.problems, NL.join(result.problems)
    assert result.exhausted and MIN_SCREENS <= result.count < CRAWL_BOUND


def test_the_checker_itself_catches_what_it_is_for():
    assert markup_problems("fine <b>x</b> &amp; y") == []
    assert markup_problems("<b>x") and markup_problems("x</b>") and markup_problems("<u>x</u>")
    assert markup_problems("a &amp;amp; b")
    assert markup_problems("x" * TELEGRAM_TEXT_LIMIT)


# --- worst-case screens, one by one ----------------------------------------------------------


async def test_hostile_home_categories_and_product_list_stay_under_the_limit(db, monkeypatch):
    _hostile_shop(db)
    flow = Flow(monkeypatch, USER, mode="mock")
    await tap(flow, "menu:show")
    assert screen_problems(*flow.screens[-1], is_home=True) == []
    await tap(flow, "cat:cats")
    assert screen_problems(*flow.screens[-1]) == []
    await tap(flow, next(d for _, d in flow.buttons if d.startswith("cat:cat:")))
    assert screen_problems(*flow.screens[-1]) == []


async def test_the_product_card_with_the_most_editions_stays_under_the_limit(db, monkeypatch):
    product = _hostile_shop(db)
    flow = Flow(monkeypatch, USER, mode="mock")
    await tap(flow, f"cat:p:{product}:0")
    assert len(flow.text) < TELEGRAM_TEXT_LIMIT
    assert screen_problems(*flow.screens[-1]) == []
    assert "more edition" in flow.text  # 60 editions: the rest are summarised


async def test_the_cart_checkout_and_summary_with_many_hostile_lines(db, monkeypatch):
    _hostile_shop(db)
    flow = Flow(monkeypatch, USER, mode="mock")
    await tap(flow, "cart:view")
    assert screen_problems(*flow.screens[-1]) == []
    await tap(flow, "cart:view:1")
    assert screen_problems(*flow.screens[-1]) == []
    await tap(flow, "co:start")
    assert screen_problems(*flow.screens[-1]) == []
    assert "more item" in flow.text
    await tap(flow, "co:pay")
    # the demo payment summary goes out as a message
    texts = [str(c.kwargs["text"]) for c in flow.sent_messages()]
    assert texts and all(markup_problems(text) == [] for text in texts)


# --- delivery of very long content ------------------------------------------------------------


def _parts_of(bot) -> list[str]:
    return [str(c.kwargs["text"]) for c in bot.send_message.await_args_list]


async def test_long_delivery_text_is_split_into_messages_that_fit_and_lose_nothing(db):
    from bot import delivery

    from .test_delivery import fake_bot, paid_order

    text = "&<>" * 700  # 2100 characters; fully escaped, far over one message
    variants_repo.set_delivery_text(TEXT, text[:2000])
    order_id = paid_order({TEXT: 1})
    bot = fake_bot()
    await delivery.deliver_order(bot, order_id)

    parts = [p for p in _parts_of(bot) if "Thank you" not in p]
    assert len(parts) >= 2
    for part in parts:
        assert markup_problems(part) == []
    body = "".join(html.unescape(p.split("\n\n", 1)[1]) for p in parts)
    assert body == text[:2000]
    assert parts[0].startswith(f"{ICON.text} <b>") and "(1/" in parts[0]


async def test_many_long_keys_are_split_into_messages_that_fit_and_lose_none(db):
    from bot import delivery

    from .test_delivery import fake_bot, paid_order

    variants_repo.set_active(KEY, True)
    order_id = paid_order({KEY: 10})
    item = orders_repo.get_with_items(order_id)[1][0]
    long_keys = [f"{i:03d}" + "&" * 197 for i in range(10)]
    db.execute("DELETE FROM license_keys WHERE order_item_id = ?", (item["id"],))
    db.commit()
    db.executemany(
        "INSERT INTO license_keys (variant_id, key_value, status, order_item_id)"
        " VALUES (?, ?, 'sold', ?)",
        [(KEY, k, item["id"]) for k in long_keys],
    )
    db.commit()
    bot = fake_bot()
    await delivery.deliver_order(bot, order_id)

    parts = [p for p in _parts_of(bot) if "Thank you" not in p]
    assert len(parts) >= 2
    delivered = [line for p in parts for line in html.unescape(unescaped(p)).split("\n")]
    for key in long_keys:
        assert key in delivered
    assert all(markup_problems(p) == [] for p in parts)


# --- the structural rule on named screens ---------------------------------------------------


async def test_every_screen_has_the_nav_row_last_except_home(db, monkeypatch):
    _lived_in_shop(db)
    flow = Flow(monkeypatch, USER, mode="mock")
    for data in ("menu:show", "help:show", "cat:cats", "cart:view", "lib:list:0", "co:start"):
        await tap(flow, data)
        text, markup = flow.screens[-1]
        assert screen_problems(text, markup, is_home=data == "menu:show") == [], data


def test_the_checkout_module_is_the_only_owner_of_the_co_prefix():
    # a tripwire for the ROUTES table in tests/screens.py staying in step with main.py
    assert checkout.route.__module__ == "bot.handlers.checkout"
