"""Inline keyboards. Callback data follows the convention
"namespace:action:arg1:arg2", e.g. "cat:cat:3:0", "adm:ord:verify:57" — every
button carries the ids it needs rather than relying on context.user_data, so
buttons keep working after a restart. Telegram caps callback_data at 64 bytes;
cb() raises ValueError on an oversized payload so an oversized payload fails loudly instead
of silently breaking taps in production.
"""

from __future__ import annotations

from collections.abc import Container

from telegram import ReplyKeyboardMarkup

CALLBACK_DATA_MAX_BYTES = 64
MAX_ROW_ID_DIGITS = 19  # len(str(2**63 - 1))
MAX_ROW_ID = 2**63 - 1  # largest SQLite INTEGER

# Persistent reply-keyboard labels for top-level customer navigation. Matched
# by EXACT full-string equality in handlers/router.py (never substring, prefix
# or case-folded) and never used as callback_data, so they aren't subject to
# the 64-byte limit above.
MENU_LABELS: tuple[str, str, str] = ("Browse Products", "Shopping Cart", "My Library")

MENU_ACTION_BY_LABEL: dict[str, str] = dict(
    zip(MENU_LABELS, ("browse", "cart", "library"), strict=True)
)


def main_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [list(MENU_LABELS)],
        resize_keyboard=True,
        is_persistent=True,
    )


def cb(*parts: str | int) -> str:
    data = ":".join(str(p) for p in parts)
    encoded_len = len(data.encode("utf-8"))
    if encoded_len > CALLBACK_DATA_MAX_BYTES:
        raise ValueError(
            f"callback_data {data!r} is {encoded_len} bytes, exceeds "
            f"{CALLBACK_DATA_MAX_BYTES}-byte Telegram limit"
        )
    return data


def parse_callback(data: str, expected_parts: int | Container[int]) -> list[str] | None:
    """Split callback_data and validate its shape before any handler acts on
    it. expected_parts is either an exact part-count, or a container (tuple,
    set, or range) of acceptable counts, for callbacks with an optional
    trailing segment. Returns None — never raises — on any mismatch, so
    malformed or hostile callback_data produces a graceful response instead
    of an unhandled ValueError/IndexError."""
    parts = data.split(":")
    if isinstance(expected_parts, int):
        if len(parts) != expected_parts:
            return None
    elif len(parts) not in expected_parts:
        return None
    return parts


def parse_cb_id(value: str) -> int | None:
    """Parse a callback_data segment that must be a row id: a canonical, ASCII,
    non-negative integer. No sign, whitespace, underscore, Unicode digit or leading
    zero ("0" itself is fine), at most 19 digits, and no larger than SQLite's 64-bit
    INTEGER (which would raise OverflowError at query time). Returns None, never
    raises, for anything else."""
    if not isinstance(value, str) or not (value.isascii() and value.isdigit()):
        return None
    if len(value) > MAX_ROW_ID_DIGITS or (len(value) > 1 and value[0] == "0"):
        return None
    n = int(value)
    return n if n <= MAX_ROW_ID else None
