from __future__ import annotations

import pytest

from bot import db as db_module
from bot.repo import categories as categories_repo
from bot.repo import license_keys as keys_repo
from bot.repo import products as products_repo
from bot.seed_demo import seed_demo


def _counts(conn) -> dict[str, int]:
    return {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608 - literals
        for table in ("categories", "products", "product_variants", "license_keys")
    }


def test_seeds_the_expected_catalogue(db):
    counts = _counts(db)
    assert counts["categories"] == 3
    assert 6 <= counts["products"] <= 9
    assert counts["product_variants"] == 2 * counts["products"]
    assert [c["name"] for c in categories_repo.list_all()] == [
        "AI Creator Assets",
        "Developer & Design Tools",
        "Guides & E-books",
    ]
    for category in categories_repo.list_all():
        assert 2 <= categories_repo.count_products(category["id"]) <= 3


def test_prices_are_whole_stars_in_range_and_content_is_fake(db):
    for row in db.execute("SELECT * FROM product_variants"):
        assert 50 <= row["price_stars"] <= 600
        if row["delivery_kind"] == "text":
            assert "example.com" in row["delivery_text"]
        assert row["delivery_kind"] in ("text", "key")


def test_key_editions_have_ten_fake_keys_except_one_with_none(db):
    stock = {
        r["id"]: keys_repo.count_available(r["id"])
        for r in db.execute("SELECT id FROM product_variants WHERE delivery_kind = 'key'")
    }
    assert sorted(stock.values()) == [0, 10, 10, 10]
    keys = [r[0] for r in db.execute("SELECT key_value FROM license_keys")]
    assert keys and all(key.startswith("NUMI-DEMO-") for key in keys)


def test_the_zero_key_edition_is_hidden_from_customers(db):
    (empty,) = [
        r
        for r in db.execute(
            "SELECT id, product_id FROM product_variants WHERE delivery_kind = 'key'"
        )
        if keys_repo.count_available(r["id"]) == 0
    ]
    assert empty["id"] not in [v["id"] for v in products_repo.list_variants(empty["product_id"])]
    assert not products_repo.is_variant_available(products_repo.get_variant(empty["id"]))


def test_every_other_edition_is_purchasable(db):
    purchasable = 0
    for row in db.execute("SELECT id FROM product_variants"):
        purchasable += products_repo.is_variant_available(products_repo.get_variant(row["id"]))
    assert purchasable == _counts(db)["product_variants"] - 1


def test_seeding_twice_changes_nothing(db):
    before = _counts(db)
    assert seed_demo() is False
    db_module.init_db()
    assert seed_demo() is False
    assert _counts(db) == before


def test_does_not_seed_when_disabled(empty_db):
    assert seed_demo() is False
    assert _counts(empty_db)["categories"] == 0


def test_does_not_touch_an_existing_catalogue(tmp_path, monkeypatch):
    from bot.config import get_settings

    db_module.close_conn()
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("SEED_DEMO", "false")
    get_settings.cache_clear()
    db_module.init_db()
    categories_repo.create("Mine")
    monkeypatch.setenv("SEED_DEMO", "true")
    get_settings.cache_clear()
    assert seed_demo() is False
    assert [c["name"] for c in categories_repo.list_all()] == ["Mine"]
    db_module.close_conn()
    get_settings.cache_clear()


@pytest.mark.parametrize("table", ["categories", "products", "product_variants"])
def test_seed_rows_are_active(db, table):
    assert (
        db.execute(
            f"SELECT COUNT(*) FROM {table} WHERE is_active = 0"  # noqa: S608 - literals
        ).fetchone()[0]
        == 0
    )
