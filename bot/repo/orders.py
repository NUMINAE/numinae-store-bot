"""Query functions for orders, order_items, and order_events.

set_status() is the only function anywhere in this codebase that writes
orders.status — Phases 4 and 5 must go through it too, which is why the
allowed-transitions map lives here as a shared module-level constant.
"""

from __future__ import annotations

import sqlite3

from ..db import get_conn, transaction
from . import cart as cart_repo
from .settings import get as get_setting

ACTIVE_STATUSES = ("awaiting_payment", "awaiting_verification", "awaiting_shipping", "rejected")

ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    # 'cancelled' here is admin-only in practice: the customer-facing cancel
    # flow in handlers/checkout.py only offers it from awaiting_payment/
    # rejected, but the admin order card needs to cancel an order stuck in
    # verification (e.g. an obviously bogus hash) without a reject-with-reason
    # detour.
    "awaiting_payment": frozenset({"awaiting_verification", "cancelled", "expired"}),
    "awaiting_verification": frozenset({"confirmed", "rejected", "cancelled"}),
    "rejected": frozenset({"awaiting_verification", "cancelled", "expired"}),
    # Phase 6 moves address collection before payment, so a new order is
    # never created with (and can never transition into) 'awaiting_shipping'
    # again — verification now goes straight to 'confirmed'. This entry is
    # legacy: it exists only so any order that was already sitting in
    # 'awaiting_shipping' before Phase 6 shipped can still complete its
    # original path via the old post-payment address prompt in
    # handlers/shipping.py, which is otherwise untouched and still live for
    # exactly that purpose. The schema's CHECK constraint still allows the
    # status for the same reason — do not remove either.
    "awaiting_shipping": frozenset({"confirmed"}),
    "confirmed": frozenset({"shipped"}),
    "shipped": frozenset({"delivered"}),
    "delivered": frozenset(),
    "cancelled": frozenset(),
    "expired": frozenset(),
}

NEEDS_ATTENTION_STATUSES = ("awaiting_verification", "awaiting_shipping", "confirmed")
CLOSED_STATUSES = ("delivered", "cancelled", "expired", "rejected")

STATUS_LABELS: dict[str, str] = {
    "awaiting_payment": "Awaiting payment",
    "awaiting_verification": "Verification pending",
    "awaiting_shipping": "Payment confirmed — preparing to ship",
    "confirmed": "Confirmed",
    "shipped": "Shipped",
    "delivered": "Delivered",
    "rejected": "Payment rejected",
    "cancelled": "Cancelled",
    "expired": "Expired",
}


class OrderError(Exception):
    """Base class for order domain errors."""


class CartEmptyError(OrderError):
    def __init__(self) -> None:
        super().__init__("Cart has no available items to check out.")


class ActiveOrderExistsError(OrderError):
    def __init__(self, order_id: int) -> None:
        self.order_id = order_id
        super().__init__(f"Customer already has an active order #{order_id}.")


class PaymentMethodUnavailableError(OrderError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"Payment method {code!r} is unavailable.")


class IllegalTransitionError(OrderError):
    def __init__(self, from_status: str, to_status: str) -> None:
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(f"Cannot transition order from {from_status!r} to {to_status!r}.")


def _assert_transition_allowed(from_status: str, to_status: str) -> None:
    if to_status not in ALLOWED_TRANSITIONS.get(from_status, frozenset()):
        raise IllegalTransitionError(from_status, to_status)


def _active_placeholders() -> str:
    return ",".join("?" for _ in ACTIVE_STATUSES)


def get_active_for_user(user_id: int) -> sqlite3.Row | None:
    conn = get_conn()
    return conn.execute(
        f"""
        SELECT * FROM orders
        WHERE user_id = ? AND status IN ({_active_placeholders()})
        ORDER BY created_at DESC
        LIMIT 1
        """,  # noqa: S608 - fragment is a fixed count of ? placeholders
        (user_id, *ACTIVE_STATUSES),
    ).fetchone()


def create_from_cart(user_id: int, payment_method_code: str, shipping_text: str) -> tuple[int, int]:
    """Create an order from the customer's currently-available cart lines.

    shipping_text is the location/address collected earlier in checkout
    (Phase 6 moved this step before payment) — the caller is responsible for
    validating it; this just writes it. Also updates customers.last_shipping_text
    here (moved from the old post-payment set_shipping(), which no longer
    runs for new orders) so "use my previous location" keeps working.

    Returns (order_id, dropped_count) — dropped_count is how many cart lines
    were excluded because their variant/product/category had become
    unavailable since being added. Raises CartEmptyError if there are no
    available lines, ActiveOrderExistsError if the customer already has one,
    or PaymentMethodUnavailableError if the chosen method is inactive or has
    no wallet address configured. All writes — the order, its item snapshots,
    the creation event, the customer's last_shipping_text, and the cart
    clear — happen in one transaction; any failure rolls all of it back and
    the cart survives.
    """
    with transaction() as conn:
        existing = conn.execute(
            f"""
            SELECT id FROM orders
            WHERE user_id = ? AND status IN ({_active_placeholders()})
            LIMIT 1
            """,  # noqa: S608 - fragment is a fixed count of ? placeholders
            (user_id, *ACTIVE_STATUSES),
        ).fetchone()
        if existing is not None:
            raise ActiveOrderExistsError(existing["id"])

        total_cart_lines = conn.execute(
            "SELECT COUNT(*) AS c FROM cart_items WHERE user_id = ?", (user_id,)
        ).fetchone()["c"]

        lines = conn.execute(
            """
            SELECT ci.variant_id, ci.quantity,
                   p.name AS product_name, v.label AS variant_label, v.price_usd
            FROM cart_items ci
            JOIN product_variants v ON v.id = ci.variant_id
            JOIN products p ON p.id = v.product_id
            JOIN categories c ON c.id = p.category_id
            WHERE ci.user_id = ?
              AND v.is_active = 1 AND p.is_active = 1 AND c.is_active = 1
            """,
            (user_id,),
        ).fetchall()
        if not lines:
            raise CartEmptyError()

        dropped_count = total_cart_lines - len(lines)

        pm = conn.execute(
            "SELECT * FROM payment_methods WHERE code = ?", (payment_method_code,)
        ).fetchone()
        if pm is None or not pm["is_active"] or not pm["wallet_address"]:
            raise PaymentMethodUnavailableError(payment_method_code)

        total_usd = sum(line["price_usd"] * line["quantity"] for line in lines)
        item_count = sum(line["quantity"] for line in lines)
        expiry_hours = int(get_setting("order_expiry_hours", "24"))  # type: ignore[arg-type]  # default given, never None

        cur = conn.execute(
            """
            INSERT INTO orders
                (user_id, total_usd, item_count, payment_method, wallet_address,
                 memo_tag, status, expires_at, shipping_text)
            VALUES (?, ?, ?, ?, ?, ?, 'awaiting_payment', datetime('now', ?), ?)
            """,
            (
                user_id,
                total_usd,
                item_count,
                pm["code"],
                pm["wallet_address"],
                pm["memo_tag"],
                f"+{expiry_hours} hours",
                shipping_text,
            ),
        )
        order_id = cur.lastrowid

        conn.execute(
            "UPDATE customers SET last_shipping_text = ? WHERE user_id = ?",
            (shipping_text, user_id),
        )

        for line in lines:
            line_total = line["price_usd"] * line["quantity"]
            conn.execute(
                """
                INSERT INTO order_items
                    (order_id, variant_id, product_name, variant_label, unit_price_usd,
                     quantity, line_total_usd)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_id,
                    line["variant_id"],
                    line["product_name"],
                    line["variant_label"],
                    line["price_usd"],
                    line["quantity"],
                    line_total,
                ),
            )

        conn.execute(
            """
            INSERT INTO order_events (order_id, from_status, to_status, actor, note)
            VALUES (?, NULL, 'awaiting_payment', 'system', 'Order created at checkout')
            """,
            (order_id,),
        )

        conn.execute("DELETE FROM cart_items WHERE user_id = ?", (user_id,))

        return order_id, dropped_count  # type: ignore[return-value]  # lastrowid is always set after an INSERT


def get(order_id: int) -> sqlite3.Row | None:
    conn = get_conn()
    return conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()


def get_with_items(order_id: int) -> tuple[sqlite3.Row | None, list[sqlite3.Row]]:
    conn = get_conn()
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if order is None:
        return None, []
    items = conn.execute(
        "SELECT * FROM order_items WHERE order_id = ? ORDER BY id", (order_id,)
    ).fetchall()
    return order, items


def list_for_user(user_id: int, limit: int, offset: int) -> list[sqlite3.Row]:
    conn = get_conn()
    return conn.execute(
        """
        SELECT * FROM orders
        WHERE user_id = ?
        ORDER BY created_at DESC
        LIMIT ? OFFSET ?
        """,
        (user_id, limit, offset),
    ).fetchall()


def count_for_user(user_id: int) -> int:
    conn = get_conn()
    row = conn.execute("SELECT COUNT(*) AS c FROM orders WHERE user_id = ?", (user_id,)).fetchone()
    return row["c"]


# ---------------------------------------------------------------------------
# Admin — order browsing and management.
# ---------------------------------------------------------------------------


def count_open_for_method(code: str) -> int:
    """Orders in awaiting_payment or rejected against this payment method —
    the deactivation blast radius. These keep working after deactivation
    since wallet_address is snapshotted onto the order at creation."""
    conn = get_conn()
    row = conn.execute(
        """
        SELECT COUNT(*) AS c FROM orders
        WHERE payment_method = ? AND status IN ('awaiting_payment', 'rejected')
        """,
        (code,),
    ).fetchone()
    return row["c"]


_FILTER_STATUSES: dict[str, tuple[str, ...]] = {
    "verify": ("awaiting_verification",),
    "ship_wait": ("awaiting_shipping",),
    "ready": ("confirmed",),
    "shipped": ("shipped",),
    "closed": CLOSED_STATUSES,
}


def _filter_where(filter_key: str) -> tuple[str, tuple]:
    if filter_key == "all":
        return "1=1", ()
    statuses = _FILTER_STATUSES.get(filter_key, ())
    placeholders = ",".join("?" for _ in statuses)
    return f"status IN ({placeholders})", statuses


def count_by_status(status: str) -> int:
    conn = get_conn()
    row = conn.execute("SELECT COUNT(*) AS c FROM orders WHERE status = ?", (status,)).fetchone()
    return row["c"]


def count_needing_attention() -> int:
    conn = get_conn()
    placeholders = ",".join("?" for _ in NEEDS_ATTENTION_STATUSES)
    row = conn.execute(
        f"SELECT COUNT(*) AS c FROM orders WHERE status IN ({placeholders})",  # noqa: S608 - fixed count of ? placeholders
        NEEDS_ATTENTION_STATUSES,
    ).fetchone()
    return row["c"]


def count_by_filter(filter_key: str) -> int:
    conn = get_conn()
    where, params = _filter_where(filter_key)
    row = conn.execute(
        f"SELECT COUNT(*) AS c FROM orders WHERE {where}",  # noqa: S608 - where is "1=1" or a fixed count of ? placeholders
        params,
    ).fetchone()
    return row["c"]


# ---------------------------------------------------------------------------
# Reminder / expiry sweep (Phase 5)
# ---------------------------------------------------------------------------


def list_awaiting_payment_for_sweep() -> list[sqlite3.Row]:
    """Candidates for the reminder/expiry sweep: awaiting_payment orders with
    a set expiry, joined with their payment method's current asset/network
    (display-only — wallet_address stays the order's own snapshotted column)
    so the reminder can be rendered without a second query per order."""
    conn = get_conn()
    return conn.execute(
        """
        SELECT o.*, pm.asset AS pm_asset, pm.network AS pm_network
        FROM orders o
        JOIN payment_methods pm ON pm.code = o.payment_method
        WHERE o.status = 'awaiting_payment' AND o.expires_at IS NOT NULL
        """
    ).fetchall()


def set_reminders_sent(order_id: int, n: int) -> None:
    with transaction() as conn:
        conn.execute(
            "UPDATE orders SET reminders_sent = ?, updated_at = datetime('now') WHERE id = ?",
            (n, order_id),
        )


def count_expired_since(days: int) -> int:
    """Orders that transitioned to 'expired' within the last N days. Reads
    order_events rather than orders.updated_at, since 'expired' is a terminal
    status (ALLOWED_TRANSITIONS['expired'] is empty) so each order has at
    most one such event — an authoritative, unambiguous count."""
    conn = get_conn()
    row = conn.execute(
        """
        SELECT COUNT(*) AS c FROM order_events
        WHERE to_status = 'expired' AND created_at >= datetime('now', ?)
        """,
        (f"-{days} days",),
    ).fetchone()
    return row["c"]


def list_by_filter(filter_key: str, limit: int, offset: int) -> list[sqlite3.Row]:
    """5-per-page order listing. The verification queue is oldest-first (the
    customer who has waited longest gets seen first); every other filter,
    including 'all', is newest-first."""
    conn = get_conn()
    where, params = _filter_where(filter_key)
    order_dir = "ASC" if filter_key == "verify" else "DESC"
    return conn.execute(
        f"SELECT * FROM orders WHERE {where} ORDER BY created_at {order_dir} LIMIT ? OFFSET ?",  # noqa: S608 - where is fixed placeholders, order_dir is the literal ASC/DESC
        (*params, limit, offset),
    ).fetchall()


def set_shipping(order_id: int, text: str) -> None:
    """Stores shipping_text, updates customers.last_shipping_text, and moves
    the order to 'confirmed' — all in one transaction. Mirrors
    submit_tx_hash()'s pattern (its own transaction, validated via the same
    _assert_transition_allowed helper) rather than calling set_status()
    separately, since this needs to be atomic with the other two writes."""
    with transaction() as conn:
        order = conn.execute(
            "SELECT status, user_id FROM orders WHERE id = ?", (order_id,)
        ).fetchone()
        if order is None:
            raise OrderError(f"Order {order_id} not found.")
        from_status = order["status"]
        to_status = "confirmed"
        _assert_transition_allowed(from_status, to_status)

        conn.execute(
            "UPDATE orders SET shipping_text = ?, status = ?, updated_at = datetime('now') WHERE id = ?",  # noqa: E501
            (text, to_status, order_id),
        )
        conn.execute(
            "UPDATE customers SET last_shipping_text = ? WHERE user_id = ?",
            (text, order["user_id"]),
        )
        conn.execute(
            """
            INSERT INTO order_events (order_id, from_status, to_status, actor, note)
            VALUES (?, ?, ?, 'customer', 'Shipping address provided')
            """,
            (order_id, from_status, to_status),
        )


def set_tracking_note(order_id: int, note: str) -> None:
    with transaction() as conn:
        conn.execute(
            "UPDATE orders SET tracking_note = ?, updated_at = datetime('now') WHERE id = ?",
            (note, order_id),
        )


def reject(order_id: int, actor: str, reason: str) -> None:
    """Moves the order to 'rejected' and stores reject_reason in one
    transaction — same pattern as set_shipping()/submit_tx_hash()."""
    with transaction() as conn:
        order = conn.execute("SELECT status FROM orders WHERE id = ?", (order_id,)).fetchone()
        if order is None:
            raise OrderError(f"Order {order_id} not found.")
        from_status = order["status"]
        to_status = "rejected"
        _assert_transition_allowed(from_status, to_status)

        conn.execute(
            "UPDATE orders SET status = ?, reject_reason = ?, updated_at = datetime('now') WHERE id = ?",  # noqa: E501
            (to_status, reason, order_id),
        )
        conn.execute(
            """
            INSERT INTO order_events (order_id, from_status, to_status, actor, note)
            VALUES (?, ?, ?, ?, ?)
            """,
            (order_id, from_status, to_status, actor, f"Rejected: {reason}"),
        )


def submit_tx_hash(order_id: int, tx_hash: str) -> None:
    with transaction() as conn:
        order = conn.execute("SELECT status FROM orders WHERE id = ?", (order_id,)).fetchone()
        if order is None:
            raise OrderError(f"Order {order_id} not found.")
        from_status = order["status"]
        to_status = "awaiting_verification"
        _assert_transition_allowed(from_status, to_status)

        conn.execute(
            """
            UPDATE orders
            SET tx_hash = ?, tx_submitted_at = datetime('now'),
                attempt_count = attempt_count + 1,
                status = ?, updated_at = datetime('now')
            WHERE id = ?
            """,
            (tx_hash, to_status, order_id),
        )
        conn.execute(
            """
            INSERT INTO order_events (order_id, from_status, to_status, actor, note)
            VALUES (?, ?, ?, 'customer', 'Transaction hash submitted')
            """,
            (order_id, from_status, to_status),
        )


def submit_photo_proof(order_id: int, file_id: str) -> None:
    """Payment proof via screenshot instead of a hash (Phase 6) — mirrors
    submit_tx_hash()'s pattern exactly, sharing attempt_count with it since
    both represent a proof-submission attempt. tx_hash is left untouched
    (stays NULL for a screenshot-only order): the partial unique index on
    (payment_method, tx_hash) already excludes NULL rows, so this never
    collides with anything."""
    with transaction() as conn:
        order = conn.execute("SELECT status FROM orders WHERE id = ?", (order_id,)).fetchone()
        if order is None:
            raise OrderError(f"Order {order_id} not found.")
        from_status = order["status"]
        to_status = "awaiting_verification"
        _assert_transition_allowed(from_status, to_status)

        conn.execute(
            """
            UPDATE orders
            SET proof_file_id = ?, attempt_count = attempt_count + 1,
                status = ?, updated_at = datetime('now')
            WHERE id = ?
            """,
            (file_id, to_status, order_id),
        )
        conn.execute(
            """
            INSERT INTO order_events (order_id, from_status, to_status, actor, note)
            VALUES (?, ?, ?, 'customer', 'Payment screenshot submitted')
            """,
            (order_id, from_status, to_status),
        )


def set_status(order_id: int, new_status: str, actor: str, note: str | None = None) -> None:
    with transaction() as conn:
        order = conn.execute("SELECT status FROM orders WHERE id = ?", (order_id,)).fetchone()
        if order is None:
            raise OrderError(f"Order {order_id} not found.")
        from_status = order["status"]
        _assert_transition_allowed(from_status, new_status)

        conn.execute(
            "UPDATE orders SET status = ?, updated_at = datetime('now') WHERE id = ?",
            (new_status, order_id),
        )
        conn.execute(
            """
            INSERT INTO order_events (order_id, from_status, to_status, actor, note)
            VALUES (?, ?, ?, ?, ?)
            """,
            (order_id, from_status, new_status, actor, note),
        )


_CANCELLABLE_STATUSES = ("awaiting_payment", "awaiting_verification", "rejected")


def cancel(order_id: int, actor: str, note: str | None = None) -> None:
    """Allowed from 'awaiting_payment', 'awaiting_verification', or
    'rejected' — once shipping starts the order is out of anyone's hands to
    cancel (there's deliberately no path out of awaiting_shipping except
    confirmed). Restores the cart from the order's item snapshots so the
    customer doesn't have to rebuild it, regardless of who cancelled it."""
    order = get(order_id)
    if order is None:
        raise OrderError(f"Order {order_id} not found.")
    if order["status"] not in _CANCELLABLE_STATUSES:
        raise IllegalTransitionError(order["status"], "cancelled")

    default_note = "Cancelled by customer" if actor == "customer" else "Cancelled by shop owner"
    set_status(order_id, "cancelled", actor, note=note or default_note)

    _, items = get_with_items(order_id)
    for item in items:
        if item["variant_id"] is not None:
            cart_repo.add(order["user_id"], item["variant_id"], item["quantity"])
