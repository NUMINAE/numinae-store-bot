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
MAX_ROW_ID = 2**63 - 1  # largest SQLite INTEGER

# Persistent reply-keyboard labels for top-level customer navigation, one
# triple per language. Matched by exact string in handlers/router.py — never
# used as callback_data, so they aren't subject to the 64-byte limit above.
# A customer can have a stale keyboard cached in the OTHER language (they
# just switched, or the bot restarted mid-session) — MENU_ACTION_BY_LABEL
# below maps every label in every language to the same action, so router.py
# matches against the full set of six, not just the current language.
MENU_LABELS: dict[str, tuple[str, str, str]] = {
    "en": ("Browse Products", "Shopping Cart", "My Library"),
    "ru": ("Просмотр товаров", "Корзина", "Моя библиотека"),
}

MENU_ACTION_BY_LABEL: dict[str, str] = {}
for _labels in MENU_LABELS.values():
    _browse, _cart, _library = _labels
    MENU_ACTION_BY_LABEL[_browse] = "browse"
    MENU_ACTION_BY_LABEL[_cart] = "cart"
    MENU_ACTION_BY_LABEL[_library] = "library"
del _labels, _browse, _cart, _library


def main_reply_keyboard(lang: str) -> ReplyKeyboardMarkup:
    labels = MENU_LABELS.get(lang, MENU_LABELS["en"])
    return ReplyKeyboardMarkup(
        [list(labels)],
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
    """Safely parse a callback_data segment expected to be a non-negative
    row id. Returns None — never raises — on non-numeric input, a negative
    value, or one too large for SQLite's 64-bit INTEGER (which would raise
    OverflowError at query time)."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if 0 <= n <= MAX_ROW_ID else None
