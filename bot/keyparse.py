"""Parsing pasted or uploaded licence keys: one key per line.

Key text is sensitive (it is what customers pay for), so nothing here echoes or
logs it: problems are reported as counts and 1-based line numbers only."""

from __future__ import annotations

from typing import NamedTuple

MAX_KEY_LENGTH = 200
MAX_KEYS_PER_BATCH = 5000

_BOM = "﻿"


class ParsedKeys(NamedTuple):
    valid: list[str]  # first occurrences, in order
    duplicates: int  # repeats within this batch that were dropped
    invalid_lines: list[int]  # 1-based line numbers; never the text
    over_limit: bool  # more than MAX_KEYS_PER_BATCH valid keys were supplied


def _has_control_characters(line: str) -> bool:
    """ASCII control characters (0x00-0x1F, 0x7F). A plain space is fine."""
    return any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in line)


def parse_keys(raw: str) -> ParsedKeys:
    """Split `raw` into keys. A leading BOM is dropped; lines split on \\r\\n
    or \\n and are stripped. Blank lines are skipped without counting as
    invalid. A line is invalid if it is over 200 characters or contains an ASCII
    control character. Valid keys are deduplicated within the batch (first
    occurrence wins) and capped at 5000; the rest are ignored and over_limit is
    set."""
    if raw.startswith(_BOM):
        raw = raw[len(_BOM) :]

    valid: list[str] = []
    seen: set[str] = set()
    invalid_lines: list[int] = []
    duplicates = 0
    over_limit = False

    for number, line in enumerate(raw.replace("\r\n", "\n").split("\n"), start=1):
        key = line.strip()
        if not key:
            continue
        if len(key) > MAX_KEY_LENGTH or _has_control_characters(key):
            invalid_lines.append(number)
            continue
        if key in seen:
            duplicates += 1
            continue
        if len(valid) >= MAX_KEYS_PER_BATCH:
            over_limit = True
            continue
        seen.add(key)
        valid.append(key)

    return ParsedKeys(valid, duplicates, invalid_lines, over_limit)
