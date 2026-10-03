"""bot/stats.py with hand-built data and exact expected numbers.

now = 2026-06-15 12:00:00 UTC, so the 7-day window is
[2026-06-08 12:00:00, 2026-06-15 12:00:00]."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from bot.stats import STATUSES, EditionSales, ModeStats, build_stats

from .helpers import add_customer

NOW = datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC)
_counter = [0]


def order(
    conn,
    total: int,
    status: str,
    mode: str = "stars",
    paid_at: str | None = None,
    refunded_at: str | None = None,
    items: tuple[tuple[str, str, int], ...] = (),
) -> int:
    _counter[0] += 1
    user_id = 9000 + _counter[0]
    add_customer(user_id)
    order_id = conn.execute(
        "INSERT INTO orders (user_id, total_stars, item_count, payment_mode, status, paid_at,"
        " refunded_at) VALUES (?, ?, 1, ?, ?, ?, ?)",
        (user_id, total, mode, status, paid_at, refunded_at),
    ).lastrowid
    for product, edition, quantity in items:
        conn.execute(
            "INSERT INTO order_items (order_id, product_name, variant_label, delivery_kind,"
            " unit_price_stars, quantity, line_total_stars) VALUES (?, ?, ?, 'text', 1, ?, ?)",
            (order_id, product, edition, quantity, quantity),
        )
    conn.commit()
    return int(order_id)


@pytest.fixture
def shop(empty_db):
    conn = empty_db
    # --- real money (stars) ---
    order(conn, 100, "delivered", paid_at="2026-06-14 10:00:00", items=(("Alpha", "One", 2),))
    order(
        conn,
        250,
        "paid",
        paid_at="2026-06-10 09:00:00",
        items=(("Alpha", "One", 1), ("Beta", "Two", 5)),
    )
    # Paid BEFORE the window, refunded INSIDE it.
    order(
        conn,
        400,
        "refunded",
        paid_at="2026-06-01 08:00:00",
        refunded_at="2026-06-12 08:00:00",
        items=(("Gamma", "X", 50),),
    )
    # Paid and refunded inside the window.
    order(conn, 60, "refunded", paid_at="2026-06-09 08:00:00", refunded_at="2026-06-09 09:00:00")
    order(conn, 999, "pending_payment", items=(("Alpha", "One", 100),))
    order(conn, 30, "cancelled")
    order(conn, 500, "delivered", paid_at="2026-05-01 08:00:00", items=(("Beta", "Two", 1),))
    # Window edges: exactly at the start (in), one second before it (out), after now (out).
    order(conn, 7, "delivered", paid_at="2026-06-08 12:00:00")
    order(conn, 11, "delivered", paid_at="2026-06-08 11:59:59")
    order(conn, 13, "delivered", paid_at="2026-06-15 12:00:01")
    # --- demo money (mock) ---
    order(
        conn,
        75,
        "delivered",
        mode="mock",
        paid_at="2026-06-14 10:00:00",
        items=(("Alpha", "One", 4), ("MockOnly", "M", 2)),
    )
    order(
        conn,
        20,
        "refunded",
        mode="mock",
        paid_at="2026-06-14 11:00:00",
        refunded_at="2026-06-14 12:00:00",
        items=(("Alpha", "One", 50),),  # refunded: never counted as sold
    )
    return conn


def test_real_money_for_the_last_seven_days_and_all_time(shop):
    stars = build_stats(NOW).modes["stars"]
    # gross 7d: 100 + 250 + 60 (paid in window, even though 60 was refunded) + 7 (edge)
    # refunded 7d: 400 (paid earlier, refunded inside) + 60
    assert stars.gross_7d == 417
    assert stars.refunded_7d == 460
    assert stars.net_7d == -43  # a refund of an earlier sale can push a window negative
    # all time: 100 + 250 + 400 + 60 + 500 + 7 + 11 (the order after `now` is excluded)
    assert stars.gross_all == 1328
    assert stars.refunded_all == 460
    assert stars.net_all == 868


def test_demo_money_is_reported_separately_from_real_money(shop):
    stats = build_stats(NOW)
    mock = stats.modes["mock"]
    assert (mock.gross_7d, mock.refunded_7d, mock.net_7d) == (95, 20, 75)
    assert (mock.gross_all, mock.refunded_all, mock.net_all) == (95, 20, 75)
    assert stats.modes["stars"].gross_all == 1328  # untouched by the mock orders
    assert set(stats.modes) == {"stars", "mock"}


def test_the_window_edges(shop):
    stars = build_stats(NOW).modes["stars"]
    # Included: the order paid exactly at the window start (7). Excluded: one second
    # before it (11, all-time only) and one second after `now` (13, nowhere).
    assert stars.gross_7d == 417 and stars.gross_all == 1328

    later = build_stats(NOW + timedelta(seconds=2)).modes["stars"]
    # `now` moved 2s on: the start-edge order (7) falls out, the order at 12:00:01 (13) is in.
    assert later.gross_7d == 417 - 7 + 13
    assert later.gross_all == 1328 + 13


def test_moving_now_forward_ages_sales_out_of_the_window(shop):
    stats = build_stats(NOW + timedelta(days=3))  # window start 2026-06-11 12:00
    stars = stats.modes["stars"]
    # in window now: the 100 order (06-14) and the 13 (06-15 12:00:01); 250 (06-10) is out
    assert stars.gross_7d == 113
    assert stars.refunded_7d == 400  # the 06-12 refund is still inside; the 06-09 one is out
    assert stars.gross_all == 1328 + 13


def test_orders_by_status_counts_everything_all_time(shop):
    by_status = build_stats(NOW).orders_by_status
    assert by_status == {
        "pending_payment": 1,
        "paid": 1,
        "delivered": 6,  # 100, 500, 7, 11, 13 and the mock 75
        "cancelled": 1,
        "expired": 0,
        "refunded": 3,  # 400, 60 and the mock 20
    }
    assert set(by_status) == set(STATUSES)


def test_top_editions_are_computed_per_payment_mode(shop):
    modes = build_stats(NOW).modes
    # Stars: Beta - Two 5 + 1 = 6; Alpha - One 2 + 1 = 3. Gamma (refunded) and the
    # pending order's 100 units are excluded, and so is every mock sale.
    assert modes["stars"].top_editions == [
        EditionSales("Beta - Two", 6),
        EditionSales("Alpha - One", 3),
    ]
    # Mock: Alpha - One 4 and MockOnly - M 2; the refunded mock order's 50 units
    # are excluded, and no stars sale leaks in.
    assert modes["mock"].top_editions == [
        EditionSales("Alpha - One", 4),
        EditionSales("MockOnly - M", 2),
    ]


def test_a_mock_only_sale_never_appears_in_the_stars_list_and_vice_versa(shop):
    modes = build_stats(NOW).modes
    assert "MockOnly - M" not in [e.label for e in modes["stars"].top_editions]
    assert "Beta - Two" not in [e.label for e in modes["mock"].top_editions]
    assert "Gamma - X" not in [e.label for m in modes.values() for e in m.top_editions]


def test_top_editions_are_limited_to_five_per_mode_with_ties_broken_by_name(shop):
    for name, units in (("Delta", 6), ("Echo", 3), ("Foxtrot", 3), ("Golf", 3), ("Hotel", 3)):
        order(shop, 1, "delivered", paid_at="2026-06-14 10:00:00", items=((name, "Ed", units),))
    modes = build_stats(NOW).modes
    assert [(e.label, e.units) for e in modes["stars"].top_editions] == [
        ("Beta - Two", 6),
        ("Delta - Ed", 6),
        ("Alpha - One", 3),
        ("Echo - Ed", 3),
        ("Foxtrot - Ed", 3),
    ]
    assert len(modes["mock"].top_editions) == 2  # untouched by the stars sales


def test_customers_total_and_new_in_the_window(empty_db):
    for user_id, created in (
        (1, "2026-01-01 00:00:00"),
        (2, "2026-06-08 12:00:00"),  # exactly the window start: new
        (3, "2026-06-08 11:59:59"),  # a second before: not new
        (4, "2026-06-15 12:00:00"),  # exactly now: new
        (5, "2026-06-15 12:00:01"),  # after now: not new (but still a customer)
    ):
        add_customer(user_id)
        empty_db.execute(
            "UPDATE customers SET created_at = ? WHERE user_id = ?", (created, user_id)
        )
    empty_db.commit()
    stats = build_stats(NOW)
    assert stats.customers_total == 5 and stats.customers_new_7d == 2


def test_an_empty_database_is_all_zeros(empty_db):
    stats = build_stats(NOW)
    assert stats.modes == {
        "stars": ModeStats(0, 0, 0, 0, 0, 0),
        "mock": ModeStats(0, 0, 0, 0, 0, 0),
    }
    assert stats.orders_by_status == dict.fromkeys(STATUSES, 0)
    assert all(m.top_editions == [] for m in stats.modes.values())
    assert (stats.customers_total, stats.customers_new_7d) == (0, 0)


def test_now_defaults_to_the_current_time(shop):
    stats = build_stats()  # real clock: all the 2026-06 sales are long in the past or future
    assert isinstance(stats.modes["stars"].gross_all, int)


def test_naive_and_non_utc_nows_are_handled(shop):
    naive = build_stats(datetime(2026, 6, 15, 12, 0, 0))  # treated as UTC
    plus_two = build_stats(datetime(2026, 6, 15, 14, 0, 0, tzinfo=timezone(timedelta(hours=2))))
    assert naive == build_stats(NOW) == plus_two


def test_a_refund_in_the_window_for_an_order_that_has_no_paid_at_is_still_counted(empty_db):
    order(empty_db, 40, "refunded", refunded_at="2026-06-14 00:00:00")
    stars = build_stats(NOW).modes["stars"]
    assert (stars.gross_7d, stars.refunded_7d, stars.net_7d) == (0, 40, -40)
