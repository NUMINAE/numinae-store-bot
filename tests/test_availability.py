"""The single availability predicate: an edition is purchasable only if the
edition, its product and its category are all active AND its delivery is
configured (text needs text, file needs a file, key needs stock)."""

from __future__ import annotations

import pytest

from bot.repo import cart as cart_repo
from bot.repo import categories as categories_repo
from bot.repo import license_keys as keys_repo
from bot.repo import products as products_repo
from bot.repo import variants as variants_repo

from .helpers import add_customer

USER = 1001


@pytest.fixture
def shop(empty_db):
    """One category / product with an edition of each delivery kind."""
    category = categories_repo.create("Cat")
    product = products_repo.create(category, "Prod")
    ids = {
        "category": category,
        "product": product,
        "text": variants_repo.create(product, "Text", 100, "text"),
        "file": variants_repo.create(product, "File", 100, "file"),
        "key": variants_repo.create(product, "Key", 100, "key"),
    }
    return ids


def _available(variant_id: int) -> bool:
    row = products_repo.get_variant(variant_id)
    assert row is not None
    return products_repo.is_variant_available(row)


def _setters(shop):
    return {
        "variant": lambda active: variants_repo.set_active(shop["text"], active),
        "product": lambda active: products_repo.set_active(shop["product"], active),
        "category": lambda active: categories_repo.set_active(shop["category"], active),
    }


# --- delivery configuration --------------------------------------------------


def test_new_editions_are_hidden_until_delivery_is_configured(shop):
    assert not _available(shop["text"])
    assert not _available(shop["file"])
    assert not _available(shop["key"])


def test_text_edition_needs_non_empty_delivery_text(shop):
    variants_repo.set_delivery_text(shop["text"], "")
    assert not _available(shop["text"])
    variants_repo.set_delivery_text(shop["text"], "   \n ")
    assert not _available(shop["text"])
    variants_repo.set_delivery_text(shop["text"], "https://example.com/x")
    assert _available(shop["text"])


def test_file_edition_needs_a_file_id(shop, empty_db):
    empty_db.execute(
        "UPDATE product_variants SET delivery_file_id = '' WHERE id = ?", (shop["file"],)
    )
    assert not _available(shop["file"])
    empty_db.execute(
        "UPDATE product_variants SET delivery_file_id = 'BQACAgIAAx' WHERE id = ?", (shop["file"],)
    )
    empty_db.commit()
    assert _available(shop["file"])


def test_delivery_content_of_the_wrong_kind_does_not_count(shop, empty_db):
    # A key edition with delivery text but no keys, a text edition with only a file id.
    variants_repo.set_delivery_text(shop["key"], "ignored")
    empty_db.execute(
        "UPDATE product_variants SET delivery_file_id = 'f' WHERE id = ?", (shop["text"],)
    )
    empty_db.commit()
    assert not _available(shop["key"])
    assert not _available(shop["text"])


def test_key_edition_needs_at_least_one_available_key(shop, empty_db):
    assert not _available(shop["key"])
    keys_repo.add_many(shop["key"], ["K1", "K2"])
    assert _available(shop["key"])
    empty_db.execute("UPDATE license_keys SET status = 'reserved' WHERE key_value = 'K1'")
    assert _available(shop["key"])
    empty_db.execute("UPDATE license_keys SET status = 'sold' WHERE key_value = 'K2'")
    empty_db.commit()
    assert not _available(shop["key"])  # zero stock → hidden


def test_revoked_keys_do_not_count_as_stock(shop, empty_db):
    keys_repo.add_many(shop["key"], ["K1"])
    empty_db.execute("UPDATE license_keys SET status = 'revoked'")
    empty_db.commit()
    assert not _available(shop["key"])


# --- the cascade -------------------------------------------------------------


@pytest.fixture
def ready(shop):
    variants_repo.set_delivery_text(shop["text"], "https://example.com/x")
    return shop


@pytest.mark.parametrize("level", ["variant", "product", "category"])
def test_deactivating_any_level_makes_it_unavailable(ready, level):
    assert _available(ready["text"])
    _setters(ready)[level](False)
    assert not _available(ready["text"])


@pytest.mark.parametrize("level", ["variant", "product", "category"])
def test_reactivation_restores_availability(ready, level):
    _setters(ready)[level](False)
    _setters(ready)[level](True)
    assert _available(ready["text"])


def test_cascade_never_writes_child_flags(ready, empty_db):
    categories_repo.set_active(ready["category"], False)
    products_repo.set_active(ready["product"], False)
    assert empty_db.execute("SELECT is_active FROM products").fetchone()[0] == 0
    assert all(r[0] == 1 for r in empty_db.execute("SELECT is_active FROM product_variants"))
    products_repo.set_active(ready["product"], True)
    categories_repo.set_active(ready["category"], True)
    assert _available(ready["text"])


def test_reactivating_a_parent_does_not_revive_an_independently_disabled_child(ready):
    variants_repo.set_active(ready["text"], False)
    categories_repo.set_active(ready["category"], False)
    categories_repo.set_active(ready["category"], True)
    assert not _available(ready["text"])


# --- listings use the same predicate -----------------------------------------


def test_customer_listings_follow_the_predicate(ready):
    category, product = ready["category"], ready["product"]
    assert category in [c["id"] for c in categories_repo.list_active()]
    assert [p["id"] for p in products_repo.list_active_in_category(category, 10, 0)] == [product]
    assert products_repo.count_active_in_category(category) == 1
    assert [v["id"] for v in products_repo.list_variants(product)] == [ready["text"]]
    assert categories_repo.count_purchasable_variants(category) == 1
    assert products_repo.count_purchasable_variants(product) == 1

    categories_repo.set_active(category, False)
    assert category not in [c["id"] for c in categories_repo.list_active()]
    assert products_repo.list_active_in_category(category, 10, 0) == []
    assert products_repo.count_active_in_category(category) == 0
    assert categories_repo.count_purchasable_variants(category) == 0
    # The admin views still see everything.
    assert category in [c["id"] for c in categories_repo.list_all()]
    assert len(products_repo.list_all_in_category(category, 10, 0)) == 1
    assert len(variants_repo.list_all_for_product(product)) == 3


def test_category_with_only_unconfigured_editions_is_hidden(shop):
    assert shop["category"] not in [c["id"] for c in categories_repo.list_active()]
    assert products_repo.list_active_in_category(shop["category"], 10, 0) == []


def test_min_price_covers_only_purchasable_editions(ready, empty_db):
    cheap = variants_repo.create(ready["product"], "Cheap", 10, "text")  # no delivery text yet
    (row,) = products_repo.list_active_in_category(ready["category"], 10, 0)
    assert row["min_price_stars"] == 100
    variants_repo.set_delivery_text(cheap, "x")
    (row,) = products_repo.list_active_in_category(ready["category"], 10, 0)
    assert row["min_price_stars"] == 10


def test_get_variant_exposes_the_predicate_and_key_stock(ready):
    keys_repo.add_many(ready["key"], ["A", "B", "C"])
    row = products_repo.get_variant(ready["key"])
    assert row["is_purchasable"] == 1
    assert row["key_stock"] == 3
    assert row["delivery_kind"] == "key"
    assert products_repo.get_variant(99999) is None


# --- cart lines use it too ---------------------------------------------------


def test_cart_summary_counts_only_available_lines(ready):
    add_customer(USER)
    keys_repo.add_many(ready["key"], ["A", "B"])
    cart_repo.add(USER, ready["text"], 1)
    cart_repo.add(USER, ready["key"], 2)
    assert cart_repo.summary(USER) == (3, 300)
    variants_repo.set_active(ready["key"], False)
    assert cart_repo.summary(USER) == (1, 100)
    flags = {
        line["variant_id"]: products_repo.is_variant_available(line)
        for line in cart_repo.list_lines(USER)
    }
    assert flags == {ready["text"]: True, ready["key"]: False}
