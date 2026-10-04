"""Order / cart summary lines shared by the checkout review, the demo payment
summary and the receipt. At most ITEM_LINES_SHOWN lines are listed, then a
"…and N more" line, so a summary of maximum-length, fully-escaped names still
fits in one Telegram message."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence

from .i18n import tn
from .render import esc, stars
from .ui_kit import ITEM_LINES_SHOWN, SEP_PRICE, trim

NAME_BUDGET = 60


def summary_lines(
    rows: Sequence[sqlite3.Row],
    name_of: Callable[[sqlite3.Row], str],
    qty_of: Callable[[sqlite3.Row], int],
    total_of: Callable[[sqlite3.Row], int],
) -> list[str]:
    """Rendered HTML lines "name × qty — ⭐ total", then "…and N more"."""
    lines = [
        f"{esc(trim(name_of(row), 2 * NAME_BUDGET + 3))} × {qty_of(row)}"
        f"{SEP_PRICE}{stars(total_of(row))}"
        for row in rows[:ITEM_LINES_SHOWN]
    ]
    if len(rows) > ITEM_LINES_SHOWN:
        lines.append(esc(tn("checkout.more_items", len(rows) - ITEM_LINES_SHOWN)))
    return lines
