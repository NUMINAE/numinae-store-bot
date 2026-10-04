"""The demo provider: no money moves. It shows an order summary under a loud
"DEMO MODE" banner with a "Pay (demo)" button whose handler (handlers/checkout)
feeds the SAME completion path a real payment uses. Never enable in production."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from ..i18n import t
from ..keyboards import cb
from ..render import bold, stars
from ..summary import summary_lines
from ..ui_kit import ICON, screen


class MockProvider:
    mode = "mock"

    async def present_checkout(
        self,
        bot: Bot,
        chat_id: int,
        order: sqlite3.Row,
        items: Sequence[sqlite3.Row],
    ) -> None:
        order_id = order["id"]
        lines = summary_lines(
            items,
            lambda i: f"{i['product_name']} — {i['variant_label']}",
            lambda i: i["quantity"],
            lambda i: i["line_total_stars"],
        )
        total = bold(t("pay.mock_total", total=stars(order["total_stars"])))
        body = "\n".join([bold(t("pay.mock_banner")), "", *lines, "", total])
        text = screen(
            ICON.paid,
            t("pay.mock_heading", id=order_id),
            body,
            step=(2, 3, "Payment"),
        )
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        t("pay.mock_pay_button"), callback_data=cb("co", "mockpay", order_id)
                    )
                ],
                [
                    InlineKeyboardButton(
                        t("pay.cancel_order_button"),
                        callback_data=cb("co", "cancel", order_id),
                    )
                ],
            ]
        )
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )

    async def refund(self, bot: Bot, user_id: int, charge_id: str) -> bool:
        return True  # nothing was charged, so there is nothing to give back
