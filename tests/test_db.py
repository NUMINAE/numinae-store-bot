from __future__ import annotations

import pytest

from bot import db as db_module

SEEDED_TABLES = (
    "settings",
    "categories",
    "products",
    "product_variants",
    "payment_methods",
)


def _counts(conn) -> dict[str, int]:
    return {
        t: conn.execute(f"SELECT COUNT(*) AS c FROM {t}").fetchone()["c"]  # noqa: S608 - literals
        for t in SEEDED_TABLES
    }


def test_init_db_seeds_catalogue(db):
    counts = _counts(db)
    assert counts["categories"] == 3
    assert counts["products"] == 9
    assert counts["product_variants"] == 27
    assert counts["payment_methods"] > 0


def test_init_db_twice_leaves_row_counts_unchanged(db):
    before = _counts(db)
    db_module.init_db()
    assert _counts(db) == before


def test_migrate_is_idempotent(db):
    before = _counts(db)
    db_module._migrate()
    db_module._migrate()
    assert _counts(db) == before


def test_migrate_upgrades_a_legacy_database(db):
    # Rebuild the shape a pre-migration database had.
    db.execute("ALTER TABLE admin_pending_input DROP COLUMN target_key")
    db.execute("ALTER TABLE orders DROP COLUMN proof_file_id")
    db.execute("ALTER TABLE customers DROP COLUMN language")
    db.execute("DELETE FROM settings WHERE key = 'notify_new_customers'")
    db.execute("INSERT INTO admin_pending_input (user_id, action) VALUES (1, 'cat_name')")
    db.commit()

    db_module._migrate()

    def columns(table: str) -> set[str]:
        return {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}  # noqa: S608

    assert "target_key" in columns("admin_pending_input")
    assert "proof_file_id" in columns("orders")
    assert "language" in columns("customers")
    row = db.execute("SELECT value FROM settings WHERE key = 'notify_new_customers'").fetchone()
    assert row["value"] == "1"
    # Pre-split pending rows cannot be reclassified, so migration discards them.
    assert db.execute("SELECT COUNT(*) AS c FROM admin_pending_input").fetchone()["c"] == 0


def test_migrate_preserves_existing_notify_setting(db):
    db.execute("UPDATE settings SET value = '0' WHERE key = 'notify_new_customers'")
    db.commit()
    db_module._migrate()
    row = db.execute("SELECT value FROM settings WHERE key = 'notify_new_customers'").fetchone()
    assert row["value"] == "0"


def test_migrate_adds_only_the_missing_notify_setting(db):
    db.execute("DELETE FROM settings WHERE key = 'notify_new_customers'")
    db.execute("UPDATE settings SET value = 'customised' WHERE key = 'welcome_text'")
    db.execute("INSERT INTO settings (key, value) VALUES ('custom_key', 'custom')")
    db.execute("DELETE FROM settings WHERE key = 'payment_instructions'")
    db.commit()
    before = {r["key"]: r["value"] for r in db.execute("SELECT key, value FROM settings")}

    db_module._migrate()

    after = {r["key"]: r["value"] for r in db.execute("SELECT key, value FROM settings")}
    assert after.pop("notify_new_customers") == "1"
    assert after == before


def test_fresh_database_gets_every_seeded_setting(db):
    import json

    seeded = json.loads(db_module._SEED_PATH.read_text(encoding="utf-8"))["settings"]
    stored = {r["key"] for r in db.execute("SELECT key FROM settings")}
    assert set(seeded) <= stored


def test_foreign_keys_are_enforced(db):
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_transaction_commits_on_success(db):
    with db_module.transaction() as conn:
        conn.execute("INSERT INTO customers (user_id) VALUES (?)", (42,))
    assert db.execute("SELECT 1 FROM customers WHERE user_id = 42").fetchone() is not None


def test_transaction_rolls_back_and_reraises_on_error(db):
    with pytest.raises(RuntimeError, match="boom"), db_module.transaction() as conn:
        conn.execute("INSERT INTO customers (user_id) VALUES (?)", (43,))
        raise RuntimeError("boom")
    assert db.execute("SELECT 1 FROM customers WHERE user_id = 43").fetchone() is None


def test_close_conn_reopens_lazily(db):
    first = db_module.get_conn()
    db_module.close_conn()
    second = db_module.get_conn()
    assert second is not first
    db_module.close_conn()  # idempotent when already closed
    db_module.close_conn()
