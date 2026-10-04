"""Delivering purchased goods. Delivery only ever runs for orders that are paid
(it is called after a recorded payment, from the sweep, and from the library's
re-send). Each item is sent independently: one failing never blocks the others,
and only a successful send stamps order_items.delivered_at. Content is read
from the CURRENT edition row regardless of is_active, so a customer who paid
keeps access to an edition that has since been deactivated."""

from __future__ import annotations

import logging
import sqlite3
from typing import NamedTuple

from telegram import Bot
from telegram.constants import ParseMode

from .i18n import t
from .render import bold, code, esc, stars
from .repo import license_keys as keys_repo
from .repo import orders as orders_repo
from .repo import variants as variants_repo
from .summary import NAME_BUDGET, summary_lines
from .ui_kit import ICON, delivery_icon, screen, trim

logger = logging.getLogger(__name__)

# Characters per delivery message, header included: under Telegram's 4096 with headroom.
MESSAGE_LIMIT = 4000

# Order ids with a delivery pass running in this process. An overlapping call
# for the same order (a retry sweep racing the payment handler's own delivery)
# returns at once, so no item is ever sent twice concurrently.
_in_flight: set[int] = set()


class DeliveryResult(NamedTuple):
    delivered: list[int]  # order_item ids sent this call
    failed: list[int]  # order_item ids that could not be sent


class MissingContent(Exception):
    """The item has nothing to send (content unset, file id empty, no keys)."""


def _item_name(item: sqlite3.Row) -> str:
    return f"{item['product_name']} — {item['variant_label']}"


def _title(item: sqlite3.Row) -> str:
    return trim(_item_name(item), 2 * NAME_BUDGET + 3)


def _chunk_budget(icon: str, title: str) -> int:
    """Room left for a body once the (worst-case "(99/99)") header is counted."""
    header = screen(icon, t("deliver.part_title", name=title, n=99, m=99))
    return MESSAGE_LIMIT - len(header) - len("\n\n")


def _text_chunks(text: str, budget: int) -> list[str]:
    """Split raw text so every piece is at most `budget` characters once
    escaped. Customers get all of it: long content becomes several messages."""
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for char in text:
        width = len(esc(char))
        if size + width > budget:
            chunks.append("".join(current))
            current, size = [], 0
        current.append(char)
        size += width
    if current:
        chunks.append("".join(current))
    return chunks


def _key_chunks(keys: list[str], budget: int) -> list[list[str]]:
    """Group keys so each group renders (as <code> lines) within `budget`."""
    groups: list[list[str]] = []
    current: list[str] = []
    size = 0
    for key in keys:
        width = len(code(key)) + 1
        if current and size + width > budget:
            groups.append(current)
            current, size = [], 0
        current.append(key)
        size += width
    if current:
        groups.append(current)
    return groups


async def _send_parts(bot: Bot, chat_id: int, icon: str, title: str, bodies: list[str]) -> None:
    """One message per body (already-rendered HTML); several get "(n/m)" titles."""
    total = len(bodies)
    for number, body in enumerate(bodies, start=1):
        heading = title if total == 1 else t("deliver.part_title", name=title, n=number, m=total)
        await bot.send_message(
            chat_id=chat_id,
            text=screen(icon, heading, body),
            parse_mode=ParseMode.HTML,
        )


async def _send_item(bot: Bot, chat_id: int, item: sqlite3.Row) -> None:
    """Send one item's goods. Raises MissingContent when there is nothing to
    send, or whatever the Telegram call raises."""
    title = _title(item)
    kind = item["delivery_kind"]
    variant = variants_repo.get(item["variant_id"]) if item["variant_id"] is not None else None

    if kind == "text":
        text = (variant["delivery_text"] or "") if variant else ""
        if not text.strip():
            raise MissingContent("delivery text is empty")
        icon = delivery_icon("text")
        bodies = [esc(chunk) for chunk in _text_chunks(text, _chunk_budget(icon, title))]
        await _send_parts(bot, chat_id, icon, title, bodies)
    elif kind == "file":
        file_id = (variant["delivery_file_id"] or "") if variant else ""
        if not file_id:
            raise MissingContent("file id is empty")
        await bot.send_document(
            chat_id=chat_id,
            document=file_id,
            caption=screen(delivery_icon("file"), title),
            parse_mode=ParseMode.HTML,
        )
    elif kind == "key":
        keys = keys_repo.get_sold_keys(item["id"])
        if len(keys) < item["quantity"]:
            raise MissingContent(f"{len(keys)} of {item['quantity']} sold keys found")
        icon = delivery_icon("key")
        groups = _key_chunks(keys, _chunk_budget(icon, title))
        bodies = ["\n".join(code(k) for k in group) for group in groups]
        await _send_parts(bot, chat_id, icon, title, bodies)
    else:
        raise MissingContent(f"unknown delivery kind {kind!r}")


async def deliver_order(bot: Bot, order_id: int) -> DeliveryResult:
    """Send every undelivered item of a paid order. When all items are out, the
    order moves to 'delivered' (via set_status) and a receipt is sent."""
    if order_id in _in_flight:
        return DeliveryResult([], [])
    _in_flight.add(order_id)
    try:
        return await _deliver_order(bot, order_id)
    finally:
        _in_flight.discard(order_id)


async def _deliver_order(bot: Bot, order_id: int) -> DeliveryResult:
    order = orders_repo.get(order_id)
    if order is None or order["status"] != "paid":
        return DeliveryResult([], [])

    chat_id = order["user_id"]
    delivered: list[int] = []
    failed: list[int] = []
    for item in orders_repo.list_undelivered_items(order_id):
        if not _still_paid(order_id):
            logger.info("Delivery of order %s stopped: it is no longer paid", order_id)
            return DeliveryResult(delivered, failed)
        try:
            await _send_item(bot, chat_id, item)
        except Exception:
            logger.error(
                "Delivery failed (order_id=%s, order_item_id=%s)",
                order_id,
                item["id"],
                exc_info=True,
            )
            failed.append(item["id"])
            continue
        if not _still_paid(order_id):
            # e.g. a refund landed while this send was in flight: change nothing.
            logger.info("Delivery of order %s stopped mid-flight: it is no longer paid", order_id)
            return DeliveryResult(delivered, failed)
        orders_repo.mark_item_delivered(item["id"])
        delivered.append(item["id"])

    if not failed:
        try:
            moved = orders_repo.finish_order_if_delivered(order_id)
        except orders_repo.IllegalTransitionError:
            logger.info("Order %s left 'paid' before it could be marked delivered", order_id)
            moved = False
        if moved:
            await _send_receipt(bot, order_id)
    return DeliveryResult(delivered, failed)


def _still_paid(order_id: int) -> bool:
    order = orders_repo.get(order_id)
    return order is not None and bool(order["status"] == "paid")


async def deliver_item(bot: Bot, order_item_id: int) -> bool:
    """Re-send one item (the library's "send again"). Changes no state: not
    delivered_at, not the order status. Only for items of paid or delivered
    orders. Returns whether the send succeeded."""
    item = orders_repo.get_item_with_order(order_item_id)
    if item is None or item["order_status"] not in ("paid", "delivered"):
        return False
    try:
        await _send_item(bot, item["user_id"], item)
    except Exception:
        logger.error(
            "Re-send failed (order_id=%s, order_item_id=%s)",
            item["order_id"],
            order_item_id,
            exc_info=True,
        )
        return False
    return True


async def _send_receipt(bot: Bot, order_id: int) -> None:
    order, items = orders_repo.get_with_items(order_id)
    if order is None:
        return
    lines = summary_lines(
        items,
        _item_name,
        lambda i: i["quantity"],
        lambda i: i["line_total_stars"],
    )
    total = bold(t("deliver.total", total=stars(order["total_stars"])))
    body = "\n".join([esc(t("deliver.receipt")), "", *lines, "", total])
    text = screen(ICON.ok, t("deliver.receipt_title", id=order_id), body, step=(3, 3, "Delivery"))
    try:
        await bot.send_message(chat_id=order["user_id"], text=text, parse_mode=ParseMode.HTML)
    except Exception:
        logger.error("Receipt send failed (order_id=%s)", order_id, exc_info=True)
