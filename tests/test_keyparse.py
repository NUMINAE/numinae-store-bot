"""bot/keyparse.py"""

from __future__ import annotations

import logging

import pytest

from bot.keyparse import MAX_KEYS_PER_BATCH, ParsedKeys, parse_keys


def test_plain_lines():
    assert parse_keys("A-1\nB-2\nC-3") == ParsedKeys(["A-1", "B-2", "C-3"], 0, [], False)


def test_crlf_and_mixed_line_endings():
    assert parse_keys("A\r\nB\r\nC\nD").valid == ["A", "B", "C", "D"]


def test_a_leading_bom_is_stripped_but_only_a_leading_one():
    assert parse_keys("﻿KEY-1\nKEY-2").valid == ["KEY-1", "KEY-2"]
    # A BOM further in is not a BOM: it stays part of the (non-control) key text.
    assert parse_keys("A\n﻿B").valid == ["A", "﻿B"]


def test_lines_are_stripped_and_blank_lines_skipped_without_counting_as_invalid():
    result = parse_keys("  A  \n\n   \n\t\nB\n\n")
    assert result.valid == ["A", "B"] and result.invalid_lines == []


def test_spaces_inside_a_key_are_allowed():
    assert parse_keys("AAAA BBBB CCCC").valid == ["AAAA BBBB CCCC"]


def test_unicode_keys_are_fine():
    assert parse_keys("κλειδί-é-日本").valid == ["κλειδί-é-日本"]


def test_hostile_strings_are_just_data():
    assert parse_keys("<b>&\n<script>alert(1)</script>\n'; DROP TABLE x;--").valid == [
        "<b>&",
        "<script>alert(1)</script>",
        "'; DROP TABLE x;--",
    ]


def test_duplicates_within_the_batch_keep_the_first_and_are_counted():
    result = parse_keys("A\nB\nA\nC\nB\nA")
    assert result.valid == ["A", "B", "C"] and result.duplicates == 3


def test_duplicates_are_exact_so_case_and_inner_spacing_differ():
    assert parse_keys("a\nA\na b\na  b").valid == ["a", "A", "a b", "a  b"]


@pytest.mark.parametrize("bad", ["x\x00y", "x\ty", "x\x1fy", "x\x7fy", "x\x0by", "x\x1b[31m"])
def test_control_characters_make_a_line_invalid(bad):
    result = parse_keys(f"GOOD\n{bad}\nALSO-GOOD")
    assert result.valid == ["GOOD", "ALSO-GOOD"] and result.invalid_lines == [2]


def test_a_lone_carriage_return_inside_a_line_is_a_control_character():
    assert parse_keys("A\rB\nC").invalid_lines == [1]


def test_over_200_characters_is_invalid_and_exactly_200_is_fine():
    result = parse_keys("k" * 200 + "\n" + "k" * 201)
    assert result.valid == ["k" * 200] and result.invalid_lines == [2]


def test_invalid_line_numbers_are_one_based_and_count_blank_lines():
    result = parse_keys("OK\n\nBAD\x00\n\nOK2\nBAD\x01")
    assert result.invalid_lines == [3, 6] and result.valid == ["OK", "OK2"]


def test_a_bom_does_not_shift_line_numbers():
    assert parse_keys("﻿OK\nBAD\x00").invalid_lines == [2]


def test_exactly_the_limit_is_accepted():
    result = parse_keys("\n".join(f"K{n}" for n in range(MAX_KEYS_PER_BATCH)))
    assert len(result.valid) == 5000 and result.over_limit is False


def test_5001_lines_keeps_5000_and_flags_over_limit():
    result = parse_keys("\n".join(f"K{n}" for n in range(5001)))
    assert len(result.valid) == 5000 and result.over_limit is True
    assert result.valid[-1] == "K4999"  # the extra is the one ignored


def test_duplicates_of_accepted_keys_past_the_limit_count_as_duplicates_not_overflow():
    lines = [f"K{n}" for n in range(5000)] + ["K0"]
    result = parse_keys("\n".join(lines))
    assert result.over_limit is False and result.duplicates == 1


def test_invalid_lines_do_not_count_toward_the_limit():
    lines = [f"K{n}" for n in range(5000)] + ["bad\x00"] * 10
    result = parse_keys("\n".join(lines))
    assert result.over_limit is False and len(result.invalid_lines) == 10


def test_empty_and_whitespace_only_input():
    assert parse_keys("") == ParsedKeys([], 0, [], False)
    assert parse_keys("﻿\r\n  \n") == ParsedKeys([], 0, [], False)


def test_key_text_is_never_logged(caplog):
    caplog.set_level(logging.DEBUG)
    result = parse_keys("SECRET-KEY-1\nSECRET-KEY-1\nSECRET\x00KEY")
    assert result.invalid_lines == [3]
    assert "SECRET" not in caplog.text
    assert "SECRET" not in repr(result.invalid_lines)
