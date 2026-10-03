from __future__ import annotations

import pytest

from bot import db as db_module


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
