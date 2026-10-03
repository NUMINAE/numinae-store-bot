from __future__ import annotations

import pytest

from bot.keyboards import (
    CALLBACK_DATA_MAX_BYTES,
    MENU_ACTION_BY_LABEL,
    MENU_LABELS,
    cb,
    main_reply_keyboard,
    parse_callback,
    parse_cb_id,
)

# --- parse_callback ---------------------------------------------------------


def test_parse_callback_splits_a_well_formed_payload():
    assert parse_callback("cat:cat:3:0", 4) == ["cat", "cat", "3", "0"]


@pytest.mark.parametrize("data", ["cat:cat:3", "cat:cat:3:0:9", "cat", "::::"])
def test_parse_callback_wrong_part_count_is_none(data):
    assert parse_callback(data, 4) is None


def test_parse_callback_empty_string_is_none():
    # "".split(":") is [""] -> one part, so any other expectation fails.
    assert parse_callback("", 4) is None
    assert parse_callback("", 2) is None
    assert parse_callback("", range(2, 5)) is None


def test_parse_callback_accepts_containers_of_counts():
    assert parse_callback("a:b", (2, 3)) == ["a", "b"]
    assert parse_callback("a:b:c", {2, 3}) == ["a", "b", "c"]
    assert parse_callback("a:b:c:d", range(2, 4)) is None


def test_parse_callback_oversized_input_does_not_raise():
    assert parse_callback("x" * 100_000, 4) is None
    assert parse_callback(":".join(["x"] * 50_000), 4) is None
    assert parse_callback("é" * 10_000 + ":1", 2) is not None  # shape only; ids checked after


# --- parse_cb_id ------------------------------------------------------------


@pytest.mark.parametrize(("value", "expected"), [("0", 0), ("7", 7), ("007", 7), (" 5 ", 5)])
def test_parse_cb_id_accepts_non_negative_integers(value, expected):
    assert parse_cb_id(value) == expected


@pytest.mark.parametrize("value", ["-1", "-0.5", "abc", "1.5", "", " ", "1e3", "0x10", "١٢٣x"])
def test_parse_cb_id_rejects_everything_else(value):
    assert parse_cb_id(value) is None


def test_parse_cb_id_tolerates_wrong_types():
    assert parse_cb_id(None) is None


def test_parse_cb_id_oversized_input_does_not_raise():
    assert parse_cb_id("9" * 100) is None  # beyond SQLite's INTEGER range
    assert parse_cb_id(str(2**63 - 1)) == 2**63 - 1
    assert parse_cb_id(str(2**63)) is None
    assert parse_cb_id("9" * 100_000 + "x") is None


# --- cb (the 64-byte guard) -------------------------------------------------


def test_cb_joins_parts():
    assert cb("cat", "cat", 3, 0) == "cat:cat:3:0"


def test_cb_allows_exactly_64_bytes():
    data = cb("a" * 64)
    assert len(data.encode()) == CALLBACK_DATA_MAX_BYTES


def test_cb_raises_over_64_bytes():
    with pytest.raises(ValueError, match="exceeds"):
        cb("a" * 65)


def test_cb_counts_bytes_not_characters():
    # 33 two-byte characters = 66 bytes but only 33 characters.
    with pytest.raises(ValueError, match="exceeds"):
        cb("é" * 33)


# --- reply keyboard labels --------------------------------------------------


def test_every_label_maps_to_an_action_in_both_languages():
    assert set(MENU_LABELS) == {"en", "ru"}
    for labels in MENU_LABELS.values():
        browse, cart, library = labels
        assert MENU_ACTION_BY_LABEL[browse] == "browse"
        assert MENU_ACTION_BY_LABEL[cart] == "cart"
        assert MENU_ACTION_BY_LABEL[library] == "library"
    assert len(MENU_ACTION_BY_LABEL) == 6


def test_labels_are_matched_exactly_only():
    label = MENU_LABELS["en"][0]
    for near_miss in (
        label.lower(),
        label.upper(),
        label + " ",
        " " + label,
        label + "!",
        label[:-1],
    ):
        assert near_miss not in MENU_ACTION_BY_LABEL


@pytest.mark.parametrize("lang", ["en", "ru"])
def test_main_reply_keyboard_uses_the_language_labels(lang):
    markup = main_reply_keyboard(lang)
    assert [b.text for b in markup.keyboard[0]] == list(MENU_LABELS[lang])
    assert markup.is_persistent
    assert markup.resize_keyboard


def test_main_reply_keyboard_falls_back_to_english():
    markup = main_reply_keyboard("xx")
    assert [b.text for b in markup.keyboard[0]] == list(MENU_LABELS["en"])
