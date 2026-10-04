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


def test_exactly_three_english_labels_map_to_actions():
    assert MENU_LABELS == ("Browse Products", "Shopping Cart", "My Library")
    browse, cart, library = MENU_LABELS
    assert MENU_ACTION_BY_LABEL[browse] == "browse"
    assert MENU_ACTION_BY_LABEL[cart] == "cart"
    assert MENU_ACTION_BY_LABEL[library] == "library"
    assert len(MENU_ACTION_BY_LABEL) == 3


def bs_u(codes: str) -> str:
    """Build a string from hex code points, so no Cyrillic literal sits in the source."""
    return "".join(chr(int(code, 16)) for code in codes.split())


def test_the_former_russian_labels_no_longer_map_to_anything():
    former = (
        bs_u("041f 0440 043e 0441 043c 043e 0442 0440 0020 0442 043e 0432 0430 0440 043e 0432"),
        bs_u("041a 043e 0440 0437 0438 043d 0430"),
        bs_u("041c 043e 044f 0020 0431 0438 0431 043b 0438 043e 0442 0435 043a 0430"),
    )
    for label in former:
        assert label not in MENU_ACTION_BY_LABEL


def test_labels_are_matched_exactly_only():
    label = MENU_LABELS[0]
    for near_miss in (
        label.lower(),
        label.upper(),
        label + " ",
        " " + label,
        label + "!",
        label[:-1],
    ):
        assert near_miss not in MENU_ACTION_BY_LABEL


def test_main_reply_keyboard_has_the_three_labels():
    markup = main_reply_keyboard()
    assert [b.text for b in markup.keyboard[0]] == list(MENU_LABELS)
    assert markup.is_persistent
    assert markup.resize_keyboard
