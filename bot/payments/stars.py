"""Telegram Stars payments: an invoice in currency XTR, no provider token, and
exactly one price line carrying the order total in whole Stars. The invoice
fields are plain text (Telegram does not parse markup in them), so nothing here
is HTML-escaped — and long text is truncated, never split mid-tag."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Sequence

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.constants import InvoiceLimit
from telegram.error import TelegramError

from ..i18n import t
from ..keyboards import cb

logger = logging.getLogger(__name__)

STARS_CURRENCY = "XTR"
ELLIPSIS = "…"


def truncate(text: str, limit: int) -> str:
    """Cut to at most `limit` characters, ending in an ellipsis when cut."""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + ELLIPSIS


def invoice_payload(order_id: int) -> str:
    payload = str(order_id)
    if len(payload.encode("utf-8")) > InvoiceLimit.MAX_PAYLOAD_LENGTH:
        raise ValueError("Invoice payload exceeds Telegram's limit.")
    return payload


def _item_name(item: sqlite3.Row) -> str:
    return f"{item['product_name']} — {item['variant_label']}"


def invoice_description(items: Sequence[sqlite3.Row], lang: str) -> str:
    listing = ", ".join(
        _item_name(i) + (f" × {i['quantity']}" if i["quantity"] > 1 else "") for i in items
    )
    return truncate(
        t("pay.invoice_description", lang, items=listing), InvoiceLimit.MAX_DESCRIPTION_LENGTH
    )


class StarsProvider:
    mode = "stars"

    async def present_checkout(
        self,
        bot: Bot,
        chat_id: int,
        order: sqlite3.Row,
        items: Sequence[sqlite3.Row],
        lang: str,
    ) -> None:
        order_id = order["id"]
        total = order["total_stars"]
        title = truncate(t("pay.invoice_title", lang, id=order_id), InvoiceLimit.MAX_TITLE_LENGTH)
        # The pay button must be the first button; anything else goes below it.
        keyboard = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(t("pay.pay_button", lang, total=total), pay=True)],
                [
                    InlineKeyboardButton(
                        t("pay.cancel_order_button", lang),
                        callback_data=cb("co", "cancel", order_id),
                    )
                ],
            ]
        )
        await bot.send_invoice(
            chat_id=chat_id,
            title=title,
            description=invoice_description(items, lang),
            payload=invoice_payload(order_id),
            currency=STARS_CURRENCY,
            prices=[LabeledPrice(title, total)],
            reply_markup=keyboard,
        )

    async def refund(self, bot: Bot, user_id: int, charge_id: str) -> bool:
        try:
            return bool(
                await bot.refund_star_payment(user_id=user_id, telegram_payment_charge_id=charge_id)
            )
        except TelegramError:
            logger.error(
                "Star refund failed (user_id=%s, charge_id=%s)", user_id, charge_id, exc_info=True
            )
            return False
