"""Query functions for the customers table."""

from __future__ import annotations

import sqlite3

from ..db import get_conn, transaction


def upsert(user_id: int, username: str | None, first_name: str | None) -> bool:
    """Insert or update the customer row. Returns True if this created a new
    row (the customer's first appearance), False if it updated an existing
    one. Checked via a plain SELECT before the INSERT, in the same
    transaction — sqlite3's rowcount is 1 either way on an ON CONFLICT
    upsert, so it can't distinguish the two on its own."""
    with transaction() as conn:
        existing = conn.execute("SELECT 1 FROM customers WHERE user_id = ?", (user_id,)).fetchone()
        conn.execute(
            """
            INSERT INTO customers (user_id, username, first_name, last_seen_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                last_seen_at = excluded.last_seen_at
            """,
            (user_id, username, first_name),
        )
        return existing is None


def get(user_id: int) -> sqlite3.Row | None:
    conn = get_conn()
    return conn.execute("SELECT * FROM customers WHERE user_id = ?", (user_id,)).fetchone()


def count_all() -> int:
    conn = get_conn()
    row = conn.execute("SELECT COUNT(*) AS c FROM customers").fetchone()
    return row["c"]


def set_language(user_id: int, language: str) -> None:
    with transaction() as conn:
        conn.execute("UPDATE customers SET language = ? WHERE user_id = ?", (language, user_id))
