"""Migration 0004 drops customers.language (the bot is English-only)."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from bot import db as db_module
from bot.db import run_migrations

MINIMUM_SQLITE = (3, 35, 0)  # ALTER TABLE ... DROP COLUMN


def _columns(conn, table: str) -> list[str]:
    return [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]  # noqa: S608 - literals


def test_the_bundled_sqlite_supports_drop_column():
    # Asserted, never skipped: if this fails the migration cannot run here.
    assert sqlite3.sqlite_version_info >= MINIMUM_SQLITE, sqlite3.sqlite_version


def test_a_database_at_0003_upgrades_and_preserves_every_other_column(tmp_path: Path):
    upto_0003 = tmp_path / "v3"
    upto_0003.mkdir()
    for name in (
        "0001_init.sql",
        "0002_order_item_delivery.sql",
        "0003_variant_file_name.sql",
    ):
        shutil.copy(db_module.MIGRATIONS_DIR / name, upto_0003)
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    assert len(run_migrations(conn, upto_0003)) == 3
    assert "language" in _columns(conn, "customers")

    # A default-language customer, a non-default one, and rows that point at them.
    conn.execute(
        "INSERT INTO customers (user_id, username, first_name, is_blocked, created_at,"
        " last_seen_at) VALUES (1, 'ann', 'Ann', 0, '2026-01-01 00:00:00', '2026-02-02 00:00:00')"
    )
    conn.execute(
        "INSERT INTO customers (user_id, username, first_name, language, is_blocked, created_at,"
        " last_seen_at) VALUES (2, 'boris', 'Boris', 'ru', 1, '2026-03-03 00:00:00',"
        " '2026-04-04 00:00:00')"
    )
    conn.execute(
        "INSERT INTO orders (user_id, total_stars, item_count, payment_mode, status)"
        " VALUES (2, 10, 1, 'mock', 'paid')"
    )
    conn.commit()
    before = {
        r["user_id"]: {k: r[k] for k in list(r.keys()) if k != "language"}
        for r in conn.execute("SELECT * FROM customers")
    }

    assert run_migrations(conn, db_module.MIGRATIONS_DIR) == ["0004_drop_customer_language.sql"]

    assert "language" not in _columns(conn, "customers")
    assert _columns(conn, "customers") == [
        "user_id",
        "username",
        "first_name",
        "is_blocked",
        "created_at",
        "last_seen_at",
    ]
    after = {r["user_id"]: dict(r) for r in conn.execute("SELECT * FROM customers")}
    assert after == before  # every other column of every row is exactly as it was
    assert after[2]["is_blocked"] == 1 and after[2]["username"] == "boris"
    # Rows referencing customers survive and the foreign key still holds.
    assert conn.execute("SELECT user_id FROM orders").fetchone()["user_id"] == 2
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert run_migrations(conn, db_module.MIGRATIONS_DIR) == []  # idempotent
    conn.close()


def test_earlier_migrations_are_untouched_and_checksums_stay_green(empty_db):
    rows = {
        r["version"]: r["checksum"] for r in empty_db.execute("SELECT * FROM schema_migrations")
    }
    assert "0004_drop_customer_language.sql" in rows
    for name in (
        "0001_init.sql",
        "0002_order_item_delivery.sql",
        "0003_variant_file_name.sql",
    ):
        sql = (db_module.MIGRATIONS_DIR / name).read_text(encoding="utf-8")
        assert rows[name] == db_module._checksum(sql)
    # 0001 still creates the column; only 0004 removes it.
    assert "language" in (db_module.MIGRATIONS_DIR / "0001_init.sql").read_text(encoding="utf-8")
    assert db_module.init_db() == []


def test_a_fresh_database_has_no_language_column(empty_db):
    assert "language" not in _columns(empty_db, "customers")
