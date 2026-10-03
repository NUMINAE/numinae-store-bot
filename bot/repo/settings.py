"""Query functions for the settings table."""

from __future__ import annotations

import sqlite3

from ..db import get_conn, transaction

EDITABLE_KEYS = (
    "welcome_text",
    "support_note",
    "reservation_minutes",
)


def get(key: str, default: str | None = None) -> str | None:
    conn = get_conn()
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set(key: str, value: str) -> None:
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )


def list_editable() -> list[sqlite3.Row]:
    """The editable settings, in EDITABLE_KEYS order (not whatever order
    SQLite happens to return)."""
    conn = get_conn()
    placeholders = ",".join("?" for _ in EDITABLE_KEYS)
    rows = conn.execute(
        f"SELECT * FROM settings WHERE key IN ({placeholders})",  # noqa: S608 - fixed count of ? placeholders
        EDITABLE_KEYS,
    ).fetchall()
    by_key = {row["key"]: row for row in rows}
    return [by_key[key] for key in EDITABLE_KEYS if key in by_key]
