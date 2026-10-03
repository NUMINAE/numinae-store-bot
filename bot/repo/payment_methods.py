"""Query functions for the payment_methods table."""

from __future__ import annotations

import sqlite3

from ..db import get_conn, transaction


class WalletNotSetError(Exception):
    """Raised by set_active(code, True) when the method has no wallet_address
    — enforced here, not just in the UI, since a live method with no address
    is a contradiction the repo layer must not allow."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"Cannot activate {code!r}: set a wallet address first.")


def list_active_assets() -> list[str]:
    """Distinct assets with at least one active, usable (non-empty wallet)
    method, ordered by each asset's lowest sort_order."""
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT asset, MIN(sort_order) AS min_sort
        FROM payment_methods
        WHERE is_active = 1 AND wallet_address != ''
        GROUP BY asset
        ORDER BY min_sort
        """
    ).fetchall()
    return [row["asset"] for row in rows]


def list_active_for_asset(asset: str) -> list[sqlite3.Row]:
    conn = get_conn()
    return conn.execute(
        """
        SELECT * FROM payment_methods
        WHERE asset = ? AND is_active = 1 AND wallet_address != ''
        ORDER BY sort_order
        """,
        (asset,),
    ).fetchall()


def get(code: str) -> sqlite3.Row | None:
    conn = get_conn()
    return conn.execute("SELECT * FROM payment_methods WHERE code = ?", (code,)).fetchone()


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------


def list_all() -> list[sqlite3.Row]:
    """Every payment method including inactive, ordered by sort_order — which
    the seed data already groups by asset."""
    conn = get_conn()
    return conn.execute("SELECT * FROM payment_methods ORDER BY sort_order, code").fetchall()


def set_wallet_address(code: str, address: str) -> None:
    with transaction() as conn:
        conn.execute(
            "UPDATE payment_methods SET wallet_address = ? WHERE code = ?", (address, code)
        )


def set_memo_tag(code: str, memo: str | None) -> None:
    with transaction() as conn:
        conn.execute("UPDATE payment_methods SET memo_tag = ? WHERE code = ?", (memo, code))


def set_active(code: str, flag: bool) -> None:
    with transaction() as conn:
        if flag:
            row = conn.execute(
                "SELECT wallet_address FROM payment_methods WHERE code = ?", (code,)
            ).fetchone()
            if row is None or not row["wallet_address"]:
                raise WalletNotSetError(code)
        conn.execute(
            "UPDATE payment_methods SET is_active = ? WHERE code = ?", (1 if flag else 0, code)
        )


def count_live() -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM payment_methods WHERE is_active = 1 AND wallet_address != ''"
    ).fetchone()
    return row["c"]
