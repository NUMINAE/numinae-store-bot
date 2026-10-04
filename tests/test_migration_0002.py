"""Migration 0002 adds order_items.delivered_at on top of a schema-v1 database."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from bot import db as db_module
from bot.db import run_migrations


def _columns(conn, table: str) -> list[str]:
    return [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]  # noqa: S608 - literals


def test_a_database_at_0001_upgrades_cleanly_and_keeps_its_rows(tmp_path: Path):
    only_0001 = tmp_path / "v1"
    only_0001.mkdir()
    shutil.copy(db_module.MIGRATIONS_DIR / "0001_init.sql", only_0001)
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    assert run_migrations(conn, only_0001) == ["0001_init.sql"]
    assert "delivered_at" not in _columns(conn, "order_items")

    conn.execute("INSERT INTO customers (user_id) VALUES (1)")
    conn.execute(
        "INSERT INTO orders (user_id, total_stars, item_count, payment_mode, status)"
        " VALUES (1, 10, 1, 'mock', 'paid')"
    )
    conn.execute(
        "INSERT INTO order_items (order_id, product_name, variant_label, delivery_kind,"
        " unit_price_stars, quantity, line_total_stars) VALUES (1, 'P', 'V', 'text', 10, 1, 10)"
    )
    conn.commit()

    assert run_migrations(conn, db_module.MIGRATIONS_DIR) == [
        "0002_order_item_delivery.sql",
        "0003_variant_file_name.sql",
        "0004_drop_customer_language.sql",
        "0005_indexes_drop_is_blocked.sql",
    ]

    assert "delivered_at" in _columns(conn, "order_items")
    row = conn.execute("SELECT product_name, delivered_at FROM order_items").fetchone()
    assert (row["product_name"], row["delivered_at"]) == ("P", None)  # existing rows: undelivered
    versions = [
        r["version"] for r in conn.execute("SELECT version FROM schema_migrations ORDER BY version")
    ]
    assert versions == [
        "0001_init.sql",
        "0002_order_item_delivery.sql",
        "0003_variant_file_name.sql",
        "0004_drop_customer_language.sql",
        "0005_indexes_drop_is_blocked.sql",
    ]
    conn.close()


def test_the_checksum_runner_stays_green_and_0001_is_untouched(empty_db):
    assert db_module.init_db() == []  # re-running is a no-op
    rows = {
        r["version"]: r["checksum"] for r in empty_db.execute("SELECT * FROM schema_migrations")
    }
    assert set(rows) >= {"0001_init.sql", "0002_order_item_delivery.sql"}
    sql = (db_module.MIGRATIONS_DIR / "0001_init.sql").read_text(encoding="utf-8")
    assert rows["0001_init.sql"] == db_module._checksum(sql)
    block = sql[sql.index("CREATE TABLE order_items") :].split(");")[0]
    assert "delivered_at" not in block  # 0001 was never edited to include the new column


def test_a_fresh_database_has_the_column(empty_db):
    assert "delivered_at" in _columns(empty_db, "order_items")
