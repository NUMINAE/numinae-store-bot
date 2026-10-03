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

from .i18n import DEFAULT_LANGUAGE, t
from .render import bold, code, esc, stars
from .repo import customers as customers_repo
from .repo import license_keys as keys_repo
from .repo import orders as orders_repo
from .repo import variants as variants_repo

logger = logging.getLogger(__name__)

# Order ids with a delivery pass running in this process. An overlapping call
# for the same order (a retry sweep racing the payment handler's own delivery)
# returns at once, so no item is ever sent twice concurrently.
_in_flight: set[int] = set()


class DeliveryResult(NamedTuple):
    delivered: list[int]  # order_item ids sent this call
    failed: list[int]  # order_item ids that could not be sent


class MissingContent(Exception):
    """The item has nothing to send (content unset, file id empty, no keys)."""


def _lang(user_id: int) -> str:
    customer = customers_repo.get(user_id)
    return customer["language"] if customer else DEFAULT_LANGUAGE


def _item_name(item: sqlite3.Row) -> str:
    return f"{item['product_name']} — {item['variant_label']}"


async def _send_item(bot: Bot, chat_id: int, item: sqlite3.Row, lang: str) -> None:
    """Send one item's goods. Raises MissingContent when there is nothing to
    send, or whatever the Telegram call raises."""
    name = bold(_item_name(item))
    kind = item["delivery_kind"]
    variant = variants_repo.get(item["variant_id"]) if item["variant_id"] is not None else None

    if kind == "text":
        text = (variant["delivery_text"] or "") if variant else ""
        if not text.strip():
            raise MissingContent("delivery text is empty")
        await bot.send_message(
            chat_id=chat_id,
            text=t("deliver.text", lang, name=name, body=esc(text)),
            parse_mode=ParseMode.HTML,
        )
    elif kind == "file":
        file_id = (variant["delivery_file_id"] or "") if variant else ""
        if not file_id:
            raise MissingContent("file id is empty")
        await bot.send_document(
            chat_id=chat_id,
            document=file_id,
            caption=t("deliver.file_caption", lang, name=name),
            parse_mode=ParseMode.HTML,
        )
    elif kind == "key":
        keys = keys_repo.get_sold_keys(item["id"])
        if len(keys) < item["quantity"]:
            raise MissingContent(f"{len(keys)} of {item['quantity']} sold keys found")
        await bot.send_message(
            chat_id=chat_id,
            text=t("deliver.keys", lang, name=name, keys="\n".join(code(k) for k in keys)),
            parse_mode=ParseMode.HTML,
        )
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
    lang = _lang(chat_id)
    delivered: list[int] = []
    failed: list[int] = []
    for item in orders_repo.list_undelivered_items(order_id):
        if not _still_paid(order_id):
            logger.info("Delivery of order %s stopped: it is no longer paid", order_id)
            return DeliveryResult(delivered, failed)
        try:
            await _send_item(bot, chat_id, item, lang)
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
            await _send_receipt(bot, order_id, lang)
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
        await _send_item(bot, item["user_id"], item, _lang(item["user_id"]))
    except Exception:
        logger.error(
            "Re-send failed (order_id=%s, order_item_id=%s)",
            item["order_id"],
            order_item_id,
            exc_info=True,
        )
        return False
    return True


async def _send_receipt(bot: Bot, order_id: int, lang: str) -> None:
    order, items = orders_repo.get_with_items(order_id)
    if order is None:
        return
    lines = "\n".join(
        t("deliver.receipt_line", lang, name=esc(_item_name(i)), qty=i["quantity"]) for i in items
    )
    text = t("deliver.receipt", lang, id=order_id, items=lines, total=stars(order["total_stars"]))
    try:
        await bot.send_message(chat_id=order["user_id"], text=text, parse_mode=ParseMode.HTML)
    except Exception:
        logger.error("Receipt send failed (order_id=%s)", order_id, exc_info=True)
