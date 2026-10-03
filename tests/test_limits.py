"""The 25000-Star order cap: the constant, create_order, the cart warning and
the checkout screen."""

from __future__ import annotations

import pytest
from telegram.constants import InvoiceLimit

from bot.handlers import cart, checkout
from bot.limits import MAX_ORDER_STARS
from bot.repo import cart as cart_repo
from bot.repo import categories as categories_repo
from bot.repo import orders as orders_repo
from bot.repo import products as products_repo
from bot.repo import variants as variants_repo
from bot.repo.orders import OrderTooLarge

from .flow import Flow
from .helpers import add_customer
from .shop import KEY, fill_cart, key_states

USER = 3001


def make_edition(price: int) -> int:
    product = products_repo.create(categories_repo.create("Big"), "Big product")
    variant = variants_repo.create(product, "Big", price, "text")
    variants_repo.set_delivery_text(variant, "https://example.com/big")
    return variant


def _counts(conn) -> tuple[int, int, int]:
    return tuple(
        conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: S608 - literals
        for t in ("orders", "order_items", "order_events")
    )


def test_the_cap_equals_telegrams_invoice_limit():
    assert MAX_ORDER_STARS == InvoiceLimit.MAX_STAR_COUNT == 25000


@pytest.mark.parametrize("mode", ["stars", "mock"])
def test_an_order_of_exactly_the_cap_succeeds(db, mode):
    edition = make_edition(MAX_ORDER_STARS)
    fill_cart(USER, {edition: 1})
    created = orders_repo.create_order(USER, mode)
    assert orders_repo.get(created.order_id)["total_stars"] == 25000


@pytest.mark.parametrize("mode", ["stars", "mock"])
def test_one_star_over_the_cap_is_rejected_and_nothing_is_touched(db, mode):
    edition = make_edition(MAX_ORDER_STARS + 1)
    fill_cart(USER, {edition: 1, KEY: 2})  # a key line, so key reservation would show
    cart_before = [(r["variant_id"], r["quantity"]) for r in cart_repo.list_lines(USER)]
    keys_before = key_states(db)
    counts_before = _counts(db)

    with pytest.raises(OrderTooLarge) as info:
        orders_repo.create_order(USER, mode)

    assert info.value.total == MAX_ORDER_STARS + 1 + 100
    assert _counts(db) == counts_before  # no order, items or events
    assert key_states(db) == keys_before  # no keys reserved
    assert [(r["variant_id"], r["quantity"]) for r in cart_repo.list_lines(USER)] == cart_before


def test_the_limit_applies_to_the_total_not_to_single_lines(db):
    edition = make_edition(MAX_ORDER_STARS)
    fill_cart(USER, {edition: 1, KEY: 1})  # 25000 + 50
    with pytest.raises(OrderTooLarge):
        orders_repo.create_order(USER, "stars")


def test_dropped_lines_do_not_count_toward_the_total(db):
    big = make_edition(MAX_ORDER_STARS + 5000)
    fill_cart(USER, {big: 1, KEY: 1})
    variants_repo.set_active(big, False)
    created = orders_repo.create_order(USER, "stars")
    assert created.dropped_count == 1


async def test_the_cart_screen_warns_when_the_subtotal_is_over_the_limit(db, monkeypatch):
    big = make_edition(MAX_ORDER_STARS + 1)
    fill_cart(USER, {big: 1})
    flow = Flow(monkeypatch, USER)
    await flow.tap(cart.route, "cart:view")
    assert "over the ⭐ 25000 limit for one order" in flow.text


async def test_no_warning_at_exactly_the_limit(db, monkeypatch):
    fill_cart(USER, {make_edition(MAX_ORDER_STARS): 1})
    flow = Flow(monkeypatch, USER)
    await flow.tap(cart.route, "cart:view")
    assert "limit" not in flow.text


async def test_checkout_shows_a_remove_some_items_screen_instead_of_the_review(db, monkeypatch):
    fill_cart(USER, {make_edition(MAX_ORDER_STARS + 1): 1})
    flow = Flow(monkeypatch, USER)
    await flow.tap(checkout.route, "co:start")
    assert "Remove some items" in flow.text and "⭐ 25001" in flow.text
    assert "Back to cart" in flow.labels()
    assert not any(label.startswith("Pay") for label in flow.labels())


async def test_a_too_large_order_that_slips_past_the_review_is_mapped_in_pay(db, monkeypatch):
    fill_cart(USER, {make_edition(MAX_ORDER_STARS + 1): 1})
    flow = Flow(monkeypatch, USER)
    await flow.tap(checkout.route, "co:pay")
    assert "Remove some items" in flow.text
    assert orders_repo.get_open_for_user(USER) is None


def test_the_edition_price_validator_states_the_limit():
    from bot.handlers.admin_common import Invalid, validate_price

    result = validate_price("25001")
    assert isinstance(result, Invalid) and "25000" in result.message
    assert not isinstance(validate_price("25000"), Invalid)


def test_existing_editions_above_the_cap_are_left_alone(db):
    edition = make_edition(MAX_ORDER_STARS + 1)
    assert variants_repo.get(edition)["price_stars"] == 25001
    add_customer(USER)
    assert products_repo.is_variant_available(products_repo.get_variant(edition))
