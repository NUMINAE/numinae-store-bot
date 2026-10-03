"""Single module-level SQLite connection, schema bootstrap, and one-time seeding.

sqlite3 is synchronous but the volume here is a few orders a day with
sub-millisecond queries, so a dedicated async driver isn't warranted. The
connection is created once with check_same_thread=False so PTB's event loop
(which runs handlers on one thread but not necessarily always the same thread
across awaits) can reuse it safely; callers are still responsible for not
interleaving writes across concurrent coroutines in ways that would corrupt
state, which the single-owner bot process here doesn't do.
"""

from __future__ import annotations

import contextlib
import json
import logging
import sqlite3
from collections.abc import Iterator
from pathlib import Path

from .config import CONFIG

logger = logging.getLogger(__name__)

_BOT_DIR = Path(__file__).resolve().parent
_SCHEMA_PATH = _BOT_DIR / "schema.sql"
_SEED_PATH = _BOT_DIR / "seed.json"

_conn: sqlite3.Connection | None = None


def get_conn() -> sqlite3.Connection:
    """Return the module-level connection, creating it on first use."""
    global _conn
    if _conn is None:
        db_path = Path(CONFIG.db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(db_path), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA foreign_keys = ON")
        _conn.execute("PRAGMA journal_mode = WAL")
    return _conn


def close_conn() -> None:
    """Close and forget the module-level connection so the next get_conn()
    reopens it. Lets tests swap in a temporary database file."""
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None


@contextlib.contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """Context manager: commits on success, rolls back on exception."""
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def init_db() -> None:
    """Create data/ if missing, apply schema.sql, run any pending migrations,
    then seed empty tables."""
    conn = get_conn()
    schema_sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    conn.commit()
    # Seed first: _seed_settings() only fills an empty table, and _migrate()
    # inserts a settings row, which would make a fresh database look seeded.
    _seed()
    _migrate()


def _migrate() -> None:
    """Idempotent migrations for databases created before schema.sql last
    changed structurally. CREATE TABLE IF NOT EXISTS above only helps a
    brand-new table; an existing one needs its own ALTER TABLE, guarded by
    checking PRAGMA table_info first so this is safe to run on every startup.
    """
    conn = get_conn()
    columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(admin_pending_input)").fetchall()
    }
    if "target_key" not in columns:
        with transaction() as txn_conn:
            txn_conn.execute("ALTER TABLE admin_pending_input ADD COLUMN target_key TEXT")
            # admin_pending_input holds at most one transient row per user;
            # existing rows predate the target_id/target_key split and can't
            # be reliably reclassified, so discard rather than migrate them.
            txn_conn.execute("DELETE FROM admin_pending_input")
        logger.info("Migrated: added admin_pending_input.target_key")

    # _seed_settings() only inserts into an empty settings table, so a
    # database already seeded before this setting existed needs its own
    # idempotent insert here — same shape as the ALTER TABLE guard above,
    # just for a settings row instead of a column.
    has_notify_setting = conn.execute(
        "SELECT 1 FROM settings WHERE key = 'notify_new_customers'"
    ).fetchone()
    if has_notify_setting is None:
        with transaction() as txn_conn:
            txn_conn.execute(
                "INSERT INTO settings (key, value) VALUES ('notify_new_customers', '1')"
            )
        logger.info("Migrated: added notify_new_customers setting")

    order_columns = {row["name"] for row in conn.execute("PRAGMA table_info(orders)").fetchall()}
    if "proof_file_id" not in order_columns:
        with transaction() as txn_conn:
            txn_conn.execute("ALTER TABLE orders ADD COLUMN proof_file_id TEXT")
        logger.info("Migrated: added orders.proof_file_id")

    customer_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(customers)").fetchall()
    }
    if "language" not in customer_columns:
        with transaction() as txn_conn:
            txn_conn.execute("ALTER TABLE customers ADD COLUMN language TEXT NOT NULL DEFAULT 'en'")
        logger.info("Migrated: added customers.language")


def _seed() -> None:
    seed = json.loads(_SEED_PATH.read_text(encoding="utf-8"))

    with transaction() as conn:
        _seed_settings(conn, seed.get("settings", {}))
        _seed_categories(conn, seed.get("categories", []))
        _seed_products(conn, seed.get("products", []))
        _seed_variants(conn, seed.get("variants", []))
        _seed_payment_methods(conn, seed.get("payment_methods", []))


def _table_is_empty(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        f"SELECT 1 FROM {table} LIMIT 1"  # noqa: S608 - table is only ever a literal from the _seed_* callers
    ).fetchone()
    return row is None


def _seed_settings(conn: sqlite3.Connection, settings: dict) -> None:
    if not _table_is_empty(conn, "settings"):
        return
    conn.executemany(
        "INSERT INTO settings (key, value) VALUES (?, ?)",
        list(settings.items()),
    )
    logger.info("Seeded %d settings", len(settings))


def _seed_categories(conn: sqlite3.Connection, categories: list[dict]) -> None:
    if not _table_is_empty(conn, "categories"):
        return
    conn.executemany(
        """
        INSERT INTO categories (id, name, description, is_active, sort_order)
        VALUES (:id, :name, :description, :is_active, :sort_order)
        """,
        categories,
    )
    logger.info("Seeded %d categories", len(categories))


def _seed_products(conn: sqlite3.Connection, products: list[dict]) -> None:
    if not _table_is_empty(conn, "products"):
        return
    conn.executemany(
        """
        INSERT INTO products (id, category_id, name, description, is_active, sort_order)
        VALUES (:id, :category_id, :name, :description, :is_active, :sort_order)
        """,
        products,
    )
    logger.info("Seeded %d products", len(products))


def _seed_variants(conn: sqlite3.Connection, variants: list[dict]) -> None:
    if not _table_is_empty(conn, "product_variants"):
        return
    conn.executemany(
        """
        INSERT INTO product_variants (product_id, label, price_usd, is_active, sort_order)
        VALUES (:product_id, :label, :price_usd, :is_active, :sort_order)
        """,
        variants,
    )
    logger.info("Seeded %d product variants", len(variants))


def _seed_payment_methods(conn: sqlite3.Connection, methods: list[dict]) -> None:
    if not _table_is_empty(conn, "payment_methods"):
        return
    conn.executemany(
        """
        INSERT INTO payment_methods
            (code, asset, asset_name, network, wallet_address, memo_tag,
             explorer_url, tx_hash_regex, is_active, sort_order)
        VALUES
            (:code, :asset, :asset_name, :network, :wallet_address, :memo_tag,
             :explorer_url, :tx_hash_regex, :is_active, :sort_order)
        """,
        methods,
    )
    logger.info("Seeded %d payment methods", len(methods))
