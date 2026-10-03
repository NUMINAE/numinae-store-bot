"""Typed-input validators for the admin screens."""

from __future__ import annotations

import pytest

from bot.handlers import admin_common, admin_settings
from bot.handlers.admin_common import Invalid, Valid


@pytest.mark.parametrize(
    ("text", "amount"),
    [
        ("75", 75),
        (" 75 ", 75),
        ("⭐75", 75),
        ("⭐ 75", 75),
        ("⭐️75", 75),
        ("1", 1),
        ("25000", 25_000),
    ],
)
def test_prices_accept_whole_stars(text, amount):
    result = admin_common.validate_price(text)
    assert result == Valid(amount, f"⭐ {amount}")


@pytest.mark.parametrize(
    "text",
    [
        "7.5",
        "7,5",
        "0",
        "⭐0",
        "00",
        "-1",
        "-⭐5",
        "25001",
        "1000000",
        "abc",
        "",
        " ",
        "⭐",
        "$5",
        "1e3",
        "5⭐",
    ],
)
def test_prices_reject_everything_else(text):
    result = admin_common.validate_price(text)
    assert isinstance(result, Invalid)
    assert result.message


def _message(result: Valid[int] | Invalid) -> str:
    assert isinstance(result, Invalid)
    return result.message


def test_price_error_messages_are_specific():
    assert "at least" in _message(admin_common.validate_price("0"))
    assert "25000" in _message(admin_common.validate_price("25001"))
    assert "whole number" in _message(admin_common.validate_price("1.5"))


def test_delivery_text_is_kept_raw_and_bounded():
    raw = "  <b>&  \nline two "
    result = admin_common.validate_delivery_text(raw)
    assert isinstance(result, Valid)
    assert result.value == raw  # not stripped, not escaped
    assert isinstance(admin_common.validate_delivery_text(""), Invalid)
    assert isinstance(admin_common.validate_delivery_text(" \n\t"), Invalid)
    assert isinstance(admin_common.validate_delivery_text("x" * 2000), Valid)
    assert isinstance(admin_common.validate_delivery_text("x" * 2001), Invalid)


def test_names_descriptions_and_sort_orders():
    assert admin_common.validate_name("  Pro  ") == Valid("Pro", "Pro")
    assert isinstance(admin_common.validate_name(""), Invalid)
    assert isinstance(admin_common.validate_name("x" * 61), Invalid)
    assert admin_common.validate_description("-") == Valid("", "(cleared)")
    assert isinstance(admin_common.validate_description("x" * 501), Invalid)
    assert admin_common.validate_sort_order("999") == Valid(999, "999")
    for bad in ("1000", "-1", "x", ""):
        assert isinstance(admin_common.validate_sort_order(bad), Invalid)
    assert isinstance(admin_common.validate_short_text("ab", 3, 5), Invalid)
    assert admin_common.validate_short_text(" abc ", 3, 5) == Valid("abc", "abc")


@pytest.mark.parametrize("text", ["5", "15", "60", " 30 "])
def test_reservation_minutes_accepts_5_to_60(text):
    result = admin_settings.validate_reservation_minutes(text)
    assert isinstance(result, Valid)
    assert result.value == text.strip()


@pytest.mark.parametrize("text", ["4", "61", "0", "-5", "5.5", "abc", "", "١٥", "1_0"])
def test_reservation_minutes_rejects_the_rest(text):
    assert isinstance(admin_settings.validate_reservation_minutes(text), Invalid)


def test_long_text_rules():
    assert isinstance(admin_settings.validate_long_text("", allow_clear=False), Invalid)
    assert isinstance(
        admin_settings.validate_long_text("-", allow_clear=False), Valid
    )  # literal "-"
    assert admin_settings.validate_long_text("-", allow_clear=True) == Valid("", "(cleared)")
    assert isinstance(admin_settings.validate_long_text("x" * 1001, allow_clear=True), Invalid)
