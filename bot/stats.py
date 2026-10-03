"""Shop statistics for the admin Stats screen.

Money and top editions are whole Stars / units reported per payment_mode so real
('stars') and demo ('mock') figures are never mixed.

Window semantics (cash-flow, not cohort): for a window ending at `now`,
  gross    = total_stars of orders whose paid_at falls in the window and whose
             status is paid, delivered or refunded (i.e. everything that was
             ever paid, including since-refunded orders);
  refunded = total_stars of orders with status refunded whose refunded_at falls
             in the window;
  net      = gross - refunded.
A refund counts in the window it HAPPENED in, so an order paid before the
window and refunded inside it lowers that window's net (it can even go
negative) while leaving the earlier window's gross untouched. The windows are
the last 7 days, [now - 7 days, now], and all time, everything up to `now`.
Timestamps are the database's UTC text datetimes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import NamedTuple

from .db import get_conn

MODES = ("stars", "mock")
STATUSES = ("pending_payment", "paid", "delivered", "cancelled", "expired", "refunded")
WINDOW_DAYS = 7
TOP_EDITIONS = 5
_PAID_STATUSES = "('paid', 'delivered', 'refunded')"  # fixed constant, not user input


class EditionSales(NamedTuple):
    label: str  # "Product - Edition"
    units: int


@dataclass(frozen=True)
class ModeStats:
    gross_7d: int
    refunded_7d: int
    net_7d: int
    gross_all: int
    refunded_all: int
    net_all: int
    # Top editions by units sold in THIS payment mode (paid or delivered orders).
    top_editions: list[EditionSales] = field(default_factory=list)


@dataclass(frozen=True)
class Stats:
    modes: dict[str, ModeStats]
    orders_by_status: dict[str, int]
    customers_total: int
    customers_new_7d: int


def _ts(moment: datetime) -> str:
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC)
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def _sum(column: str, status_clause: str, mode: str, start: str | None, end: str) -> int:
    """Sum of total_stars for `mode` where `column` is within [start, end]."""
    sql = (
        "SELECT COALESCE(SUM(total_stars), 0) AS n FROM orders"  # noqa: S608 - column and status clause are fixed constants
        f" WHERE payment_mode = ? AND status {status_clause} AND {column} <= ?"
    )
    params: list[object] = [mode, end]
    if start is not None:
        sql += f" AND {column} >= ?"
        params.append(start)
    return int(get_conn().execute(sql, params).fetchone()["n"])


def _top_editions(mode: str) -> list[EditionSales]:
    """Top editions by units sold among this mode's paid or delivered orders."""
    rows = get_conn().execute(
        """
        SELECT oi.product_name AS product_name, oi.variant_label AS variant_label,
               SUM(oi.quantity) AS units
        FROM order_items oi
        JOIN orders o ON o.id = oi.order_id
        WHERE o.status IN ('paid', 'delivered') AND o.payment_mode = ?
        GROUP BY oi.product_name, oi.variant_label
        ORDER BY units DESC, oi.product_name, oi.variant_label
        LIMIT ?
        """,
        (mode, TOP_EDITIONS),
    )
    return [
        EditionSales(f"{row['product_name']} - {row['variant_label']}", int(row["units"]))
        for row in rows
    ]


def _mode_stats(mode: str, start: str, end: str) -> ModeStats:
    gross_7d = _sum("paid_at", f"IN {_PAID_STATUSES}", mode, start, end)
    refunded_7d = _sum("refunded_at", "= 'refunded'", mode, start, end)
    gross_all = _sum("paid_at", f"IN {_PAID_STATUSES}", mode, None, end)
    refunded_all = _sum("refunded_at", "= 'refunded'", mode, None, end)
    return ModeStats(
        gross_7d,
        refunded_7d,
        gross_7d - refunded_7d,
        gross_all,
        refunded_all,
        gross_all - refunded_all,
        _top_editions(mode),
    )


def build_stats(now: datetime | None = None) -> Stats:
    """Compute every figure for the Stats screen. `now` is injectable for tests."""
    moment = now if now is not None else datetime.now(UTC)
    end = _ts(moment)
    start = _ts(moment - timedelta(days=WINDOW_DAYS))
    conn = get_conn()

    by_status = dict.fromkeys(STATUSES, 0)
    for row in conn.execute("SELECT status, COUNT(*) AS n FROM orders GROUP BY status"):
        by_status[row["status"]] = row["n"]

    customers_total = int(conn.execute("SELECT COUNT(*) AS n FROM customers").fetchone()["n"])
    customers_new = int(
        conn.execute(
            "SELECT COUNT(*) AS n FROM customers WHERE created_at >= ? AND created_at <= ?",
            (start, end),
        ).fetchone()["n"]
    )
    return Stats(
        modes={mode: _mode_stats(mode, start, end) for mode in MODES},
        orders_by_status=by_status,
        customers_total=customers_total,
        customers_new_7d=customers_new,
    )
