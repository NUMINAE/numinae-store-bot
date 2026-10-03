"""Plain CRUD behaviour of the catalogue, customer, settings and pending-input repos."""

from __future__ import annotations

from bot.repo import admin_pending as admin_pending_repo
from bot.repo import categories as categories_repo
from bot.repo import customers as customers_repo
from bot.repo import products as products_repo
from bot.repo import settings as settings_repo
from bot.repo import variants as variants_repo

# --- categories -------------------------------------------------------------


def test_category_create_appends_with_sort_gap(db):
    last_sort = max(c["sort_order"] for c in categories_repo.list_all())
    new_id = categories_repo.create("Herbal")
    row = categories_repo.get(new_id)
    assert (row["name"], row["is_active"], row["description"]) == ("Herbal", 1, "")
    assert row["sort_order"] == last_sort + 10
    assert categories_repo.list_all()[-1]["id"] == new_id
    assert categories_repo.count_all() == 4


def test_category_updates(db):
    categories_repo.update_name(1, "Renamed")
    categories_repo.update_description(1, "Desc")
    categories_repo.update_sort_order(1, 99)
    row = categories_repo.get(1)
    assert (row["name"], row["description"], row["sort_order"]) == ("Renamed", "Desc", 99)
    assert categories_repo.list_all()[-1]["id"] == 1  # sort order respected


def test_category_counts(db):
    assert categories_repo.count_products(1) == 3
    assert categories_repo.count_purchasable_variants(1) > 0
    products_repo.set_active(1, False)
    assert categories_repo.count_products(1) == 3  # inactive still counted
    after = categories_repo.count_purchasable_variants(1)
    assert after < 9


def test_category_get_missing_is_none(db):
    assert categories_repo.get(9999) is None


# --- products ---------------------------------------------------------------


def test_product_create_and_updates(db):
    new_id = products_repo.create(1, "Fresh Tea")
    row = products_repo.get(new_id)
    assert (row["category_id"], row["name"], row["is_active"]) == (1, "Fresh Tea", 1)
    products_repo.update_name(new_id, "Fresher")
    products_repo.update_description(new_id, "d")
    products_repo.update_sort_order(new_id, 5)
    products_repo.update_category(new_id, 2)
    row = products_repo.get(new_id)
    assert (row["name"], row["description"], row["sort_order"], row["category_id"]) == (
        "Fresher",
        "d",
        5,
        2,
    )


def test_product_counts_and_pagination(db):
    assert products_repo.count_all() == 9
    assert products_repo.count_all_in_category(1) == 3
    assert products_repo.count_variants(1) == 3
    assert products_repo.count_active_variants(1) == 3
    variants_repo.set_active(1, False)
    assert products_repo.count_variants(1) == 3
    assert products_repo.count_active_variants(1) == 2
    page1 = products_repo.list_all_in_category(1, 2, 0)
    page2 = products_repo.list_all_in_category(1, 2, 2)
    assert [p["id"] for p in page1 + page2] == [1, 2, 3]
    assert len(products_repo.list_active_in_category(1, 2, 2)) == 1


def test_list_variants_only_returns_active_ones(db):
    variants_repo.set_active(1, False)
    assert 1 not in [v["id"] for v in products_repo.list_variants(1)]
    assert 1 in [v["id"] for v in variants_repo.list_all_for_product(1)]


def test_get_variant_joins_parents(db):
    row = products_repo.get_variant(1)
    assert row["product_name"] == "Persian Saffron Black Tea"
    assert row["category_name"] == "Iranian Tea"
    assert products_repo.get_variant(9999) is None


# --- variants ---------------------------------------------------------------


def test_variant_create_and_updates(db):
    new_id = variants_repo.create(1, "1kg", 2500)
    row = variants_repo.get(new_id)
    assert (row["label"], row["price_usd"], row["is_active"]) == ("1kg", 2500, 1)
    variants_repo.update_label(new_id, "2kg")
    variants_repo.update_price(new_id, 4000)
    variants_repo.update_sort_order(new_id, 7)
    row = variants_repo.get(new_id)
    assert (row["label"], row["price_usd"], row["sort_order"]) == ("2kg", 4000, 7)
    assert variants_repo.list_all_for_product(1)[-1]["id"] == new_id


def test_variant_get_missing_is_none(db):
    assert variants_repo.get(9999) is None


# --- customers --------------------------------------------------------------


def test_customer_upsert_reports_first_appearance(db):
    assert customers_repo.upsert(1001, "ann", "Ann") is True
    assert customers_repo.upsert(1001, "ann2", "Ann B") is False
    row = customers_repo.get(1001)
    assert (row["username"], row["first_name"], row["language"]) == ("ann2", "Ann B", "en")
    assert customers_repo.count_all() == 1


def test_customer_language(db):
    customers_repo.upsert(1001, None, None)
    customers_repo.set_language(1001, "ru")
    assert customers_repo.get(1001)["language"] == "ru"


def test_customer_get_missing_is_none(db):
    assert customers_repo.get(1) is None


# --- settings ---------------------------------------------------------------


def test_settings_get_set_and_default(db):
    assert settings_repo.get("nope") is None
    assert settings_repo.get("nope", "fallback") == "fallback"
    settings_repo.set("welcome_text", "Hi")
    settings_repo.set("welcome_text", "Hello")  # upsert, not duplicate
    assert settings_repo.get("welcome_text") == "Hello"
    count = db.execute("SELECT COUNT(*) FROM settings WHERE key = 'welcome_text'").fetchone()[0]
    assert count == 1


def test_list_editable_follows_editable_keys_order_and_skips_others(db):
    for key in reversed(settings_repo.EDITABLE_KEYS):
        settings_repo.set(key, f"v-{key}")
    settings_repo.set("not_editable", "x")
    rows = settings_repo.list_editable()
    assert [r["key"] for r in rows] == list(settings_repo.EDITABLE_KEYS)


# --- admin_pending ----------------------------------------------------------


def test_admin_pending_roundtrip_and_overwrite(db):
    assert admin_pending_repo.get(1) is None
    admin_pending_repo.set(1, "cat_name", target_id=5, context_id=2, pending_value="v")
    row = admin_pending_repo.get(1)
    assert (row["action"], row["target_id"], row["target_key"]) == ("cat_name", 5, None)
    assert (row["context_id"], row["pending_value"]) == (2, "v")


def test_admin_pending_overwrite_clears_stale_target(db):
    admin_pending_repo.set(1, "cat_name", target_id=5)
    admin_pending_repo.set(1, "setting_edit", target_key="welcome_text")
    row = admin_pending_repo.get(1)
    assert (row["action"], row["target_id"], row["target_key"]) == (
        "setting_edit",
        None,
        "welcome_text",
    )
    assert db.execute("SELECT COUNT(*) FROM admin_pending_input").fetchone()[0] == 1


def test_admin_pending_clear(db):
    admin_pending_repo.set(1, "cat_name")
    admin_pending_repo.set(2, "cat_name")
    admin_pending_repo.clear(1)
    assert admin_pending_repo.get(1) is None
    assert admin_pending_repo.get(2) is not None
