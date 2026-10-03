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
from ..render import bold, esc, stars


class MockProvider:
    mode = "mock"

    async def present_checkout(
        self,
        bot: Bot,
        chat_id: int,
        order: sqlite3.Row,
        items: Sequence[sqlite3.Row],
        lang: str,
    ) -> None:
        order_id = order["id"]
        lines = [
            bold(t("pay.mock_banner", lang)),
            "",
            bold(t("pay.mock_heading", lang, id=order_id)),
        ]
        for item in items:
            name = esc(f"{item['product_name']} — {item['variant_label']}")
            lines.append(f"{name} × {item['quantity']} — {stars(item['line_total_stars'])}")
        lines += ["", bold(t("pay.mock_total", lang, total=stars(order["total_stars"])))]
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        t("pay.mock_pay_button", lang), callback_data=cb("co", "mockpay", order_id)
                    )
                ],
                [
                    InlineKeyboardButton(
                        t("pay.cancel_order_button", lang),
                        callback_data=cb("co", "cancel", order_id),
                    )
                ],
            ]
        )
        await bot.send_message(
            chat_id=chat_id,
            text="\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )

    async def refund(self, bot: Bot, user_id: int, charge_id: str) -> bool:
        return True  # nothing was charged, so there is nothing to give back
