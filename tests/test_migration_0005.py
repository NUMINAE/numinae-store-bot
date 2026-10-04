"""Migration 0005: indexes for the order_item_id and order_id lookups, and the unused
customers.is_blocked column is dropped."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from bot import db as db_module
from bot.db import run_migrations
from bot.repo import license_keys as keys_repo

UPTO_0004 = (
    "0001_init.sql",
    "0002_order_item_delivery.sql",
    "0003_variant_file_name.sql",
    "0004_drop_customer_language.sql",
)
# sha256 of each applied migration (line endings normalised). Applied migrations are
# immutable: if one of these changes, someone edited a file that is already deployed.
PINNED_CHECKSUMS = {
    "0001_init.sql": ("b6bbf87b51c9e8643435b4f961802e2c2e516cef42654e00f77db373df2487c4"),
    "0002_order_item_delivery.sql": (
        "ae33ea016f7ad6c0430c007b4c95b2decba6a07a3775369941b001c9f848df72"
    ),
    "0003_variant_file_name.sql": (
        "caee33257a28379cdbeadfbcd3272fb5107949d9b49185374cf596e25b28808c"
    ),
    "0004_drop_customer_language.sql": (
        "28376e3e7a7837ad66ba1a5ec25b3f55bfb3127b5f3c2e9d5e4132853b612dea"
    ),
}
NEW = "0005_indexes_drop_is_blocked.sql"


def _columns(conn, table: str) -> list[str]:
    return [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]  # noqa: S608 - literals


def _indexes(conn) -> set[str]:
    return {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}


def _plan(conn, sql: str, params=()) -> str:
    rows = conn.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()
    return " | ".join(r["detail"] for r in rows)


def _populated_0004(tmp_path: Path) -> sqlite3.Connection:
    folder = tmp_path / "v4"
    folder.mkdir()
    for name in UPTO_0004:
        shutil.copy(db_module.MIGRATIONS_DIR / name, folder)
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    assert len(run_migrations(conn, folder)) == 4
    assert "is_blocked" in _columns(conn, "customers")
    assert not {"idx_license_keys_order_item", "idx_order_events_order"} & _indexes(conn)

    conn.execute(
        "INSERT INTO customers (user_id, username, first_name, is_blocked, created_at,"
        " last_seen_at) VALUES (1, 'ann', 'Ann', 0, '2026-01-01 00:00:00', '2026-02-02 00:00:00')"
    )
    conn.execute(
        "INSERT INTO customers (user_id, username, first_name, is_blocked, created_at,"
        " last_seen_at) VALUES (2, NULL, 'Boris', 1, '2026-03-03 00:00:00', '2026-04-04 00:00:00')"
    )
    conn.execute("INSERT INTO categories (name) VALUES ('C')")
    conn.execute("INSERT INTO products (category_id, name) VALUES (1, 'P')")
    conn.execute(
        "INSERT INTO product_variants (product_id, label, price_stars, delivery_kind)"
        " VALUES (1, 'V', 10, 'key')"
    )
    conn.execute(
        "INSERT INTO orders (user_id, total_stars, item_count, payment_mode, status,"
        " telegram_payment_charge_id) VALUES (1, 10, 1, 'stars', 'paid', 'ch_1')"
    )
    conn.execute(
        "INSERT INTO order_items (order_id, variant_id, product_name, variant_label,"
        " delivery_kind, unit_price_stars, quantity, line_total_stars)"
        " VALUES (1, 1, 'P', 'V', 'key', 10, 1, 10)"
    )
    conn.execute(
        "INSERT INTO license_keys (variant_id, key_value, status, order_item_id)"
        " VALUES (1, 'KEY-ONE', 'sold', 1)"
    )
    conn.execute(
        "INSERT INTO order_events (order_id, from_status, to_status, actor, note)"
        " VALUES (1, NULL, 'paid', 'system', 'n')"
    )
    conn.commit()
    return conn


def test_a_database_at_0004_upgrades_and_keeps_all_data(tmp_path: Path):
    conn = _populated_0004(tmp_path)
    before = {
        table: [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY 1")]  # noqa: S608
        for table in ("orders", "order_items", "license_keys", "order_events", "products")
    }
    customers_before = {
        r["user_id"]: {k: r[k] for k in r.keys() if k != "is_blocked"}  # noqa: SIM118 - sqlite3.Row
        for r in conn.execute("SELECT * FROM customers")
    }

    assert run_migrations(conn, db_module.MIGRATIONS_DIR) == [NEW]

    assert "is_blocked" not in _columns(conn, "customers")
    assert _columns(conn, "customers") == [
        "user_id",
        "username",
        "first_name",
        "created_at",
        "last_seen_at",
    ]
    customers_after = {r["user_id"]: dict(r) for r in conn.execute("SELECT * FROM customers")}
    assert customers_after == customers_before  # every other column of every row survived
    for table, rows in before.items():
        assert [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY 1")] == rows  # noqa: S608
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert {"idx_license_keys_order_item", "idx_order_events_order"} <= _indexes(conn)
    assert run_migrations(conn, db_module.MIGRATIONS_DIR) == []  # idempotent
    conn.close()


def test_a_fresh_database_has_both_indexes_and_no_is_blocked(empty_db):
    assert {"idx_license_keys_order_item", "idx_order_events_order"} <= _indexes(empty_db)
    assert "is_blocked" not in _columns(empty_db, "customers")
    assert NEW in {r["version"] for r in empty_db.execute("SELECT version FROM schema_migrations")}


def test_earlier_migration_checksums_are_unchanged(empty_db):
    stored = {
        r["version"]: r["checksum"] for r in empty_db.execute("SELECT * FROM schema_migrations")
    }
    for name, pinned in PINNED_CHECKSUMS.items():
        sql = (db_module.MIGRATIONS_DIR / name).read_text(encoding="utf-8")
        assert db_module._checksum(sql) == pinned, f"{name} was edited after being applied"
        assert stored[name] == pinned
    assert stored[NEW] == db_module._checksum(
        (db_module.MIGRATIONS_DIR / NEW).read_text(encoding="utf-8")
    )
    assert db_module.init_db() == []  # the runner's own tamper check stays green


# The statements the payment path runs, built from the same fragment the repo uses.
ITEMS = keys_repo._ORDER_ITEMS
KEY_QUERIES = {
    "pre-checkout count (count_reserved_for_order)": (
        f"SELECT COUNT(*) AS n FROM license_keys WHERE status = 'reserved'"  # noqa: S608
        f" AND order_item_id IN ({ITEMS})"
    ),
    "payment: reserved -> sold (sell_reserved_for_order_in)": (
        f"UPDATE license_keys SET status = 'sold', reserved_until = NULL"  # noqa: S608
        f" WHERE status = 'reserved' AND order_item_id IN ({ITEMS})"
    ),
    "expiry/cancel: release (release_for_order_in)": (
        f"UPDATE license_keys SET status = 'available', order_item_id = NULL,"  # noqa: S608
        f" reserved_until = NULL WHERE status = 'reserved' AND order_item_id IN ({ITEMS})"
    ),
    "refund: revoke (revoke_for_order_in)": (
        f"UPDATE license_keys SET status = 'revoked'"  # noqa: S608
        f" WHERE status = 'sold' AND order_item_id IN ({ITEMS})"
    ),
}


@pytest.mark.parametrize("label", list(KEY_QUERIES))
def test_the_order_item_in_subquery_uses_the_new_index(empty_db, label):
    plan = _plan(empty_db, KEY_QUERIES[label], (1,))
    assert "idx_license_keys_order_item" in plan, plan
    assert "SCAN license_keys" not in plan, plan


def test_delivery_key_lookup_by_item_uses_the_index(empty_db):
    plan = _plan(
        empty_db,
        "SELECT key_value FROM license_keys WHERE status = 'sold' AND order_item_id = ?"
        " ORDER BY id",
        (1,),
    )
    assert "idx_license_keys_order_item" in plan, plan


def test_the_order_card_events_query_uses_the_new_index(empty_db):
    plan = _plan(
        empty_db, "SELECT * FROM order_events WHERE order_id = ? ORDER BY id DESC LIMIT 5", (1,)
    )
    assert "idx_order_events_order" in plan, plan
