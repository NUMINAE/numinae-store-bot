"""Scenario helpers for order tests, built on the demo catalogue.

Demo editions: 1 (text, 75), 2 (text, 250), 7/8/9 (key editions, 10 keys each,
50/200/150 Stars), 10 (key edition, no keys)."""

from __future__ import annotations

from bot.repo import cart as cart_repo
from bot.repo import orders as orders_repo

from .helpers import add_customer

TEXT = 1
KEY = 7  # 10 keys, 50 Stars


def fill_cart(user_id: int, lines: dict[int, int]) -> None:
    add_customer(user_id)
    for variant_id, qty in lines.items():
        cart_repo.add(user_id, variant_id, qty)


def place_order(user_id: int, lines: dict[int, int] | None = None, mode: str = "stars") -> int:
    """Fill the cart and create the order; returns the order id."""
    fill_cart(user_id, lines or {TEXT: 1})
    return orders_repo.create_order(user_id, mode).order_id


def pay_result(
    order_id: int, charge_id: str | None = None, currency: str = "XTR"
) -> orders_repo.PaymentResult:
    order = orders_repo.get(order_id)
    assert order is not None
    return orders_repo.complete_payment(
        order_id, order["user_id"], charge_id or f"ch_{order_id}", order["total_stars"], currency
    )


def pay(
    order_id: int, charge_id: str | None = None, currency: str = "XTR"
) -> orders_repo.PaymentOutcome:
    return pay_result(order_id, charge_id, currency).outcome


def key_states(conn, variant_id: int = KEY) -> dict[str, int]:
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM license_keys WHERE variant_id = ? GROUP BY status",
        (variant_id,),
    )
    return {r["status"]: r["n"] for r in rows}


def expire_now(conn, order_id: int) -> None:
    """Pretend the reservation window has passed."""
    conn.execute(
        "UPDATE orders SET expires_at = datetime('now', '-1 minute') WHERE id = ?", (order_id,)
    )
    conn.execute(
        "UPDATE license_keys SET reserved_until = datetime('now', '-1 minute')"
        " WHERE status = 'reserved' AND order_item_id IN"
        " (SELECT id FROM order_items WHERE order_id = ?)",
        (order_id,),
    )
    conn.commit()
