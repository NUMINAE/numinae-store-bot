"""Query functions for admin_pending_input — the owner's in-flight typed-input
state for the admin catalogue manager. Lives in the database (not
context.user_data) so an edit survives a bot restart. One row per user; starting
a new pending input overwrites the old one via the upsert in set().

A pending row is keyed by exactly one of target_id or target_key, never both:
target_id (INTEGER) for category/product/variant/order ids, target_key (TEXT)
for payment method codes and setting keys. set() always writes both columns
from its arguments, so starting a target_key-based pending correctly clears
any stale target_id from a previous pending (and vice versa).
"""

from __future__ import annotations

import sqlite3

from ..db import get_conn, transaction


def set(
    user_id: int,
    action: str,
    target_id: int | None = None,
    target_key: str | None = None,
    context_id: int | None = None,
    pending_value: str | None = None,
) -> None:
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO admin_pending_input
                (user_id, action, target_id, target_key, context_id, pending_value, created_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(user_id) DO UPDATE SET
                action = excluded.action,
                target_id = excluded.target_id,
                target_key = excluded.target_key,
                context_id = excluded.context_id,
                pending_value = excluded.pending_value,
                created_at = excluded.created_at
            """,
            (user_id, action, target_id, target_key, context_id, pending_value),
        )


def get(user_id: int) -> sqlite3.Row | None:
    conn = get_conn()
    return conn.execute(
        "SELECT * FROM admin_pending_input WHERE user_id = ?", (user_id,)
    ).fetchone()


def clear(user_id: int) -> None:
    with transaction() as conn:
        conn.execute("DELETE FROM admin_pending_input WHERE user_id = ?", (user_id,))
