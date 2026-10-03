from __future__ import annotations

from bot.repo import categories as categories_repo
from bot.repo import license_keys as keys_repo
from bot.repo import products as products_repo
from bot.repo import variants as variants_repo


def _key_variant() -> int:
    category = categories_repo.create("C")
    product = products_repo.create(category, "P")
    return variants_repo.create(product, "Personal", 100, "key")


def test_add_many_counts_what_was_added(empty_db):
    variant = _key_variant()
    assert keys_repo.add_many(variant, ["A", "B", "C"]) == 3
    assert keys_repo.count_available(variant) == 3


def test_add_many_dedupes_within_input_and_against_stock(empty_db):
    variant = _key_variant()
    assert keys_repo.add_many(variant, ["A", "A", " A ", "B"]) == 2
    assert keys_repo.add_many(variant, ["B", "C"]) == 1
    assert keys_repo.count_available(variant) == 3


def test_add_many_skips_blanks(empty_db):
    variant = _key_variant()
    assert keys_repo.add_many(variant, ["", "  ", "X"]) == 1


def test_same_key_value_is_allowed_on_different_editions(empty_db):
    first, second = _key_variant(), _key_variant()
    assert keys_repo.add_many(first, ["SHARED"]) == 1
    assert keys_repo.add_many(second, ["SHARED"]) == 1


def test_count_available_ignores_other_statuses_and_editions(empty_db):
    variant, other = _key_variant(), _key_variant()
    keys_repo.add_many(variant, ["A", "B", "C"])
    keys_repo.add_many(other, ["Z"])
    empty_db.execute("UPDATE license_keys SET status = 'sold' WHERE key_value = 'A'")
    empty_db.execute("UPDATE license_keys SET status = 'reserved' WHERE key_value = 'B'")
    empty_db.commit()
    assert keys_repo.count_available(variant) == 1
    assert keys_repo.count_available(other) == 1


def test_add_many_of_nothing(empty_db):
    assert keys_repo.add_many(_key_variant(), []) == 0
