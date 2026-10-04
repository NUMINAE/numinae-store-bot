"""Migration 0003 adds product_variants.delivery_file_name on top of a 0002 database."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from bot import db as db_module
from bot.db import run_migrations


def _columns(conn, table: str) -> list[str]:
    return [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]  # noqa: S608 - literals


def test_a_database_at_0002_upgrades_cleanly_and_keeps_its_rows(tmp_path: Path):
    upto_0002 = tmp_path / "v2"
    upto_0002.mkdir()
    for name in ("0001_init.sql", "0002_order_item_delivery.sql"):
        shutil.copy(db_module.MIGRATIONS_DIR / name, upto_0002)
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    assert run_migrations(conn, upto_0002) == ["0001_init.sql", "0002_order_item_delivery.sql"]
    assert "delivery_file_name" not in _columns(conn, "product_variants")

    conn.execute("INSERT INTO categories (name) VALUES ('C')")
    conn.execute("INSERT INTO products (category_id, name) VALUES (1, 'P')")
    conn.execute(
        "INSERT INTO product_variants (product_id, label, price_stars, delivery_kind,"
        " delivery_file_id) VALUES (1, 'V', 10, 'file', 'EXISTING_FILE_ID')"
    )
    conn.commit()

    assert run_migrations(conn, db_module.MIGRATIONS_DIR) == [
        "0003_variant_file_name.sql",
        "0004_drop_customer_language.sql",
        "0005_indexes_drop_is_blocked.sql",
    ]

    assert "delivery_file_name" in _columns(conn, "product_variants")
    row = conn.execute(
        "SELECT delivery_file_id, delivery_file_name FROM product_variants"
    ).fetchone()
    assert (row["delivery_file_id"], row["delivery_file_name"]) == ("EXISTING_FILE_ID", None)
    assert run_migrations(conn, db_module.MIGRATIONS_DIR) == []  # and it is idempotent
    conn.close()


def test_earlier_migrations_are_untouched_and_the_checksums_stay_green(empty_db):
    rows = {
        r["version"]: r["checksum"] for r in empty_db.execute("SELECT * FROM schema_migrations")
    }
    assert set(rows) == {
        "0001_init.sql",
        "0002_order_item_delivery.sql",
        "0003_variant_file_name.sql",
        "0004_drop_customer_language.sql",
        "0005_indexes_drop_is_blocked.sql",
    }
    for name in ("0001_init.sql", "0002_order_item_delivery.sql"):
        sql = (db_module.MIGRATIONS_DIR / name).read_text(encoding="utf-8")
        assert rows[name] == db_module._checksum(sql)
    assert "delivery_file_name" not in (db_module.MIGRATIONS_DIR / "0001_init.sql").read_text()
    assert db_module.init_db() == []


def test_a_fresh_database_has_the_column(empty_db):
    assert "delivery_file_name" in _columns(empty_db, "product_variants")
