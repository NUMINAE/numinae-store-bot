"""The admin Stats screen ("adm:st"): real (Stars) and demo (mock) money kept in
separate sections, then orders by status and customers. The definitions live in
bot/stats.py."""

from __future__ import annotations

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from ..keyboards import cb
from ..render import bold, esc, stars
from ..stats import STATUSES, ModeStats, Stats, build_stats
from ..ui import show_screen
from ..ui_kit import ICON, SEP_META, SEP_PRICE, nav_row, screen, trim

EDITION_BUDGET = 123  # a product name and an edition label, trimmed

_MODE_TITLES = {
    "stars": "Real money (Telegram Stars)",
    "mock": "Demo (mock) — not real money",
}


def _money(label: str, gross: int, refunded: int, net: int) -> str:
    return (
        f"{label}: gross {stars(gross)}{SEP_META}refunded {stars(refunded)}"
        f"{SEP_META}net {stars(net)}"
    )


def _mode_section(mode: str, data: ModeStats) -> list[str]:
    lines = [
        bold(_MODE_TITLES[mode]),
        _money("Last 7 days", data.gross_7d, data.refunded_7d, data.net_7d),
        _money("All time", data.gross_all, data.refunded_all, data.net_all),
        "Top editions:",
    ]
    if data.top_editions:
        lines += [
            f"{rank}. {esc(trim(edition.label, EDITION_BUDGET))}{SEP_PRICE}{edition.units}"
            for rank, edition in enumerate(data.top_editions, start=1)
        ]
    else:
        lines.append("(no sales yet)")
    return lines


def render_stats(stats: Stats) -> str:
    lines: list[str] = []
    for mode in ("stars", "mock"):
        lines += _mode_section(mode, stats.modes[mode]) + [""]
    lines.append(bold("Orders by status"))
    lines += [
        f"{status.replace('_', ' ')}: {stats.orders_by_status[status]}" for status in STATUSES
    ]
    lines += [
        "",
        f"Customers: {stats.customers_total} total, {stats.customers_new_7d} new in 7 days",
    ]
    return screen(ICON.stats, "Stats", "\n".join(lines), crumbs=["Admin"])


async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = InlineKeyboardMarkup([nav_row(back=cb("adm", "home"))])
    await show_screen(update, context, text=render_stats(build_stats()), keyboard=keyboard)
