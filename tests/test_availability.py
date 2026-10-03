"""The availability predicate: a variant is purchasable only if the variant,
its product and the product's category are all active."""

from __future__ import annotations

import pytest

from bot.repo import cart as cart_repo
from bot.repo import categories as categories_repo
from bot.repo import products as products_repo
from bot.repo import variants as variants_repo

from .helpers import add_customer

USER = 1001
VARIANT = 1  # seed: product 1 (category 1), "100g", 600 cents
PRODUCT = 1
CATEGORY = 1


def _setters():
    return {
        "variant": lambda active: variants_repo.set_active(VARIANT, active),
        "product": lambda active: products_repo.set_active(PRODUCT, active),
        "category": lambda active: categories_repo.set_active(CATEGORY, active),
    }


def _line_available() -> bool:
    (line,) = cart_repo.list_lines(USER)
    return products_repo.is_variant_available(line)


@pytest.fixture
def cart_with_variant(db):
    add_customer(USER)
    cart_repo.add(USER, VARIANT, 2)


def test_fresh_line_is_available(cart_with_variant):
    assert _line_available()
    assert cart_repo.summary(USER) == (2, 1200)


@pytest.mark.parametrize("level", ["variant", "product", "category"])
def test_deactivating_any_level_makes_the_line_unavailable(cart_with_variant, level):
    _setters()[level](False)
    assert not _line_available()
    assert cart_repo.summary(USER) == (0, 0)


@pytest.mark.parametrize("level", ["variant", "product", "category"])
def test_reactivation_restores_availability(cart_with_variant, level):
    _setters()[level](False)
    _setters()[level](True)
    assert _line_available()
    assert cart_repo.summary(USER) == (2, 1200)


@pytest.mark.parametrize("level", ["variant", "product", "category"])
def test_get_variant_exposes_the_same_predicate(db, level):
    assert products_repo.is_variant_available(products_repo.get_variant(VARIANT))
    _setters()[level](False)
    assert not products_repo.is_variant_available(products_repo.get_variant(VARIANT))


def _active_flags(conn):
    return (
        [tuple(r) for r in conn.execute("SELECT id, is_active FROM products ORDER BY id")],
        [tuple(r) for r in conn.execute("SELECT id, is_active FROM product_variants ORDER BY id")],
    )


def test_category_deactivation_never_writes_child_flags(db):
    before = _active_flags(db)
    categories_repo.set_active(CATEGORY, False)
    assert _active_flags(db) == before


def test_product_deactivation_never_writes_variant_flags(db):
    _, variants_before = _active_flags(db)
    products_repo.set_active(PRODUCT, False)
    assert _active_flags(db)[1] == variants_before


def test_reactivating_a_parent_does_not_reactivate_an_independently_disabled_child(db):
    variants_repo.set_active(VARIANT, False)
    categories_repo.set_active(CATEGORY, False)
    categories_repo.set_active(CATEGORY, True)
    assert variants_repo.get(VARIANT)["is_active"] == 0


def test_customer_listings_hide_unavailable_levels(db):
    assert CATEGORY in [c["id"] for c in categories_repo.list_active()]
    categories_repo.set_active(CATEGORY, False)
    assert CATEGORY not in [c["id"] for c in categories_repo.list_active()]
    assert products_repo.list_active_in_category(CATEGORY, 10, 0) == []
    assert products_repo.count_active_in_category(CATEGORY) == 0
    # The admin view still sees everything.
    assert CATEGORY in [c["id"] for c in categories_repo.list_all()]
    assert len(products_repo.list_all_in_category(CATEGORY, 10, 0)) == 3


def test_category_with_no_active_variants_is_hidden(db):
    for product_id in (1, 2, 3):
        for v in variants_repo.list_all_for_product(product_id):
            variants_repo.set_active(v["id"], False)
    assert CATEGORY not in [c["id"] for c in categories_repo.list_active()]
    assert categories_repo.count_purchasable_variants(CATEGORY) == 0


def test_product_listing_carries_min_price_of_active_variants(db):
    rows = products_repo.list_active_in_category(CATEGORY, 10, 0)
    first = next(r for r in rows if r["id"] == PRODUCT)
    assert first["min_price_usd"] == 600
    variants_repo.set_active(VARIANT, False)
    rows = products_repo.list_active_in_category(CATEGORY, 10, 0)
    first = next(r for r in rows if r["id"] == PRODUCT)
    assert first["min_price_usd"] > 600
