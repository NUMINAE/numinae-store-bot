"""Shared helpers for the admin screens: typed-input validators and the
common prompt/reprompt/cancel-button UI, plus small status-label helpers.

Kept in its own module — rather than living in admin_catalogue.py — so
admin_catalogue.py and admin_orders.py can both use it without importing
each other.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import ContextTypes

from ..keyboards import cb
from ..render import usd
from ..ui import show_screen

MAX_PRICE_CENTS = 1_000_000  # $10,000.00
_PRICE_RE = re.compile(r"^\$?\s*(\d+(?:\.\d{1,2})?)\s*$")


def validate_name(text: str) -> tuple[bool, str | None, str]:
    cleaned = text.strip()
    if not cleaned or len(cleaned) > 60:
        return False, None, "Please send a name between 1 and 60 characters."
    return True, cleaned, cleaned


def validate_description(text: str) -> tuple[bool, str | None, str]:
    cleaned = text.strip()
    if cleaned == "-":
        return True, "", "(cleared)"
    if len(cleaned) > 500:
        return False, None, "Descriptions must be 500 characters or fewer. Please retype."
    return True, cleaned, cleaned if cleaned else "(empty)"


def validate_price(text: str) -> tuple[bool, int | None, str]:
    cleaned = text.strip()
    match = _PRICE_RE.match(cleaned)
    if not match:
        return (
            False,
            None,
            "That doesn't look like a price. Send something like 14, 14.50, or $14.50.",
        )
    try:
        dollars = Decimal(match.group(1))
    except InvalidOperation:
        return (
            False,
            None,
            "That doesn't look like a price. Send something like 14, 14.50, or $14.50.",
        )
    cents = int((dollars * 100).to_integral_value(rounding=ROUND_HALF_UP))
    if cents <= 0:
        return False, None, "Price must be greater than $0."
    if cents > MAX_PRICE_CENTS:
        return False, None, "Price must be $10,000 or less."
    return True, cents, usd(cents)


def validate_sort_order(text: str) -> tuple[bool, int | None, str]:
    cleaned = text.strip()
    try:
        value = int(cleaned)
    except ValueError:
        return False, None, "Please send a whole number between 0 and 999."
    if not (0 <= value <= 999):
        return False, None, "Please send a whole number between 0 and 999."
    return True, value, str(value)


def validate_short_text(text: str, min_len: int, max_len: int) -> tuple[bool, str | None, str]:
    cleaned = text.strip()
    if not (min_len <= len(cleaned) <= max_len):
        return False, None, f"Please send between {min_len} and {max_len} characters."
    return True, cleaned, cleaned


def cancel_button() -> InlineKeyboardButton:
    return InlineKeyboardButton("Cancel", callback_data=cb("adm", "cancel"))


async def prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    keyboard = InlineKeyboardMarkup([[cancel_button()]])
    await show_screen(update, context, text=text, keyboard=keyboard)
    # Every admin typed-input prompt goes through here, so this is the one
    # place that needs to hide the customer reply keyboard while the owner
    # types — the prompt above already used its reply_markup slot for the
    # Cancel button, whether it edited or sent fresh.
    await context.bot.send_message(
        chat_id=update.effective_chat.id, text="Type below.", reply_markup=ReplyKeyboardRemove()
    )


async def reprompt_after_invalid(
    update: Update, context: ContextTypes.DEFAULT_TYPE, error: str
) -> None:
    """Validation failed: keep the pending row, explain, let them retype."""
    keyboard = InlineKeyboardMarkup([[cancel_button()]])
    await update.message.reply_text(error, reply_markup=keyboard)


def status_word(is_active) -> str:
    return "active" if is_active else "HIDDEN"


def toggle_label(is_active) -> str:
    return "Deactivate" if is_active else "Activate"
