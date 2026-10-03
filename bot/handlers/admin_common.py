"""Shared helpers for the admin screens: typed-input validators and the
common prompt/reprompt/cancel-button UI, plus small status-label helpers.

Kept in its own module — rather than living in admin_catalogue.py — so
admin_catalogue.py and admin_settings.py can both use it without importing
each other.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Generic, TypeVar

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import ContextTypes

from ..keyboards import cb
from ..limits import MAX_ORDER_STARS
from ..render import stars
from ..tg import require_message
from ..ui import show_screen

T = TypeVar("T")


@dataclass(frozen=True)
class Valid(Generic[T]):
    """A typed-input value that passed validation, plus a short human preview."""

    value: T
    preview: str


@dataclass(frozen=True)
class Invalid:
    message: str


MAX_PRICE_STARS = MAX_ORDER_STARS  # a single edition can never exceed one invoice
MAX_DELIVERY_TEXT_LEN = 2000
_PRICE_RE = re.compile(r"^(?:⭐\ufe0f?\s*)?(\d{1,12})$")
_PRICE_HELP = "Send a whole number of Stars, e.g. 75 or ⭐75."


def validate_name(text: str) -> Valid[str] | Invalid:
    cleaned = text.strip()
    if not cleaned or len(cleaned) > 60:
        return Invalid("Please send a name between 1 and 60 characters.")
    return Valid(cleaned, cleaned)


def validate_description(text: str) -> Valid[str] | Invalid:
    cleaned = text.strip()
    if cleaned == "-":
        return Valid("", "(cleared)")
    if len(cleaned) > 500:
        return Invalid("Descriptions must be 500 characters or fewer. Please retype.")
    return Valid(cleaned, cleaned if cleaned else "(empty)")


def validate_price(text: str) -> Valid[int] | Invalid:
    """Whole Telegram Stars only: "75" or "⭐75". Decimals, zero, negatives and
    anything over MAX_PRICE_STARS are rejected."""
    match = _PRICE_RE.match(text.strip())
    if not match:
        return Invalid(f"That doesn't look like a price. {_PRICE_HELP}")
    amount = int(match.group(1))
    if amount <= 0:
        return Invalid("Price must be at least ⭐1.")
    if amount > MAX_PRICE_STARS:
        return Invalid(f"Price must be {stars(MAX_PRICE_STARS)} or less.")
    return Valid(amount, stars(amount))


def validate_delivery_text(text: str) -> Valid[str] | Invalid:
    """Stored raw (escaped only when rendered); 1–2000 characters."""
    if not text.strip() or len(text) > MAX_DELIVERY_TEXT_LEN:
        return Invalid(f"Please send between 1 and {MAX_DELIVERY_TEXT_LEN} characters.")
    return Valid(text, "saved")


def validate_sort_order(text: str) -> Valid[int] | Invalid:
    cleaned = text.strip()
    try:
        value = int(cleaned)
    except ValueError:
        return Invalid("Please send a whole number between 0 and 999.")
    if not (0 <= value <= 999):
        return Invalid("Please send a whole number between 0 and 999.")
    return Valid(value, str(value))


def validate_short_text(text: str, min_len: int, max_len: int) -> Valid[str] | Invalid:
    cleaned = text.strip()
    if not (min_len <= len(cleaned) <= max_len):
        return Invalid(f"Please send between {min_len} and {max_len} characters.")
    return Valid(cleaned, cleaned)


def cancel_button() -> InlineKeyboardButton:
    return InlineKeyboardButton("Cancel", callback_data=cb("adm", "cancel"))


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[cancel_button()]])


async def prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    keyboard = InlineKeyboardMarkup([[cancel_button()]])
    await show_screen(update, context, text=text, keyboard=keyboard)
    # Every admin typed-input prompt goes through here, so this is the one
    # place that needs to hide the customer reply keyboard while the owner
    # types — the prompt above already used its reply_markup slot for the
    # Cancel button, whether it edited or sent fresh.
    chat = update.effective_chat
    if chat is None:
        return
    await context.bot.send_message(
        chat_id=chat.id, text="Type below.", reply_markup=ReplyKeyboardRemove()
    )


async def reprompt_after_invalid(
    update: Update, context: ContextTypes.DEFAULT_TYPE, error: str
) -> None:
    """Validation failed: keep the pending row, explain, let them retype."""
    keyboard = InlineKeyboardMarkup([[cancel_button()]])
    await require_message(update).reply_text(error, reply_markup=keyboard)


def status_word(is_active) -> str:
    return "active" if is_active else "HIDDEN"


def toggle_label(is_active) -> str:
    return "Deactivate" if is_active else "Activate"
