"""Query functions for license_keys. Only stock entry and counting live here
for now; reservation and sale arrive with checkout."""

from __future__ import annotations

from ..db import get_conn, transaction


def add_many(variant_id: int, keys: list[str]) -> int:
    """Insert the keys as available stock. Surrounding whitespace is stripped,
    blanks are skipped, and duplicates — within the input or already stored for
    this edition — are ignored. Returns how many were actually added."""
    unique = list(dict.fromkeys(key.strip() for key in keys if key.strip()))
    added = 0
    with transaction() as conn:
        for key in unique:
            cur = conn.execute(
                "INSERT OR IGNORE INTO license_keys (variant_id, key_value) VALUES (?, ?)",
                (variant_id, key),
            )
            added += cur.rowcount
    return added


def count_available(variant_id: int) -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM license_keys WHERE variant_id = ? AND status = 'available'",
        (variant_id,),
    ).fetchone()
    return int(row["n"])
