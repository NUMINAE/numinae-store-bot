"""The design language: icons and the small building blocks every screen uses.

Screen anatomy (top to bottom):
    <icon> <b>Title</b>
    Breadcrumb › Trail            (optional)
    Step 2 of 3 · Payment         (optional)

    body ...
and an inline keyboard whose rows go: primary actions, secondary actions, then
the nav row (‹ Back, 🏠 Home) last. " · " separates metadata, " — " separates a
label from its price.

Escaping contract for screen(): `title`, `crumbs` and `step` are PLAIN text and
are escaped inside screen(); `body` is ALREADY-RENDERED HTML (built with the
render helpers, escaping each dynamic value once) and is NOT escaped again, so
nothing is ever double-escaped. Raw admin-entered text that is going to be
escaped by the caller is shortened with trim() BEFORE escaping, so a cut can
never land inside an HTML tag or entity.

ICON is the only place emoji for screens are defined; handlers and strings use
it (strings through "{i_<name>}" placeholders that i18n fills in)."""

from __future__ import annotations

import html
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass

from telegram import InlineKeyboardButton

ELLIPSIS = "…"
SEP_META = " · "  # between metadata items
SEP_PRICE = " — "  # between a label and its price
CRUMB_SEP = " › "

ITEM_LINES_SHOWN = 5  # order/cart summaries show this many lines, then "…and N more"

HOME_CALLBACK = "menu:show"
NOOP_CALLBACK = "noop"


@dataclass(frozen=True)
class Icons:
    browse: str = "🛍️"
    cart: str = "🛒"
    library: str = "📚"
    stars: str = "⭐"
    home: str = "🏠"
    back: str = "‹"
    # delivery kinds
    file: str = "📄"
    key: str = "🔑"
    text: str = "💬"
    # order statuses
    pending_payment: str = "⏳"
    paid: str = "💳"
    delivered: str = "📦"
    cancelled: str = "🚫"
    expired: str = "⌛"
    refunded: str = "↩️"
    # feedback
    warning: str = "⚠️"
    ok: str = "✅"
    alert: str = "🚨"
    info: str = "ℹ️"
    demo: str = "🧪"
    # controls
    prev: str = "◀"
    next: str = "▶"
    minus: str = "−"
    plus: str = "+"
    remove: str = "✕"
    admin: str = "🛠️"
    orders: str = "🧾"
    stats: str = "📊"
    settings: str = "⚙️"
    catalogue: str = "🗂️"


ICON = Icons()

# "{i_ok}"-style placeholders available to every i18n string.
ICON_FIELDS: dict[str, str] = {f"i_{name}": value for name, value in asdict(ICON).items()}

DELIVERY_ICONS = {"file": ICON.file, "key": ICON.key, "text": ICON.text}
STATUS_ICONS = {
    "pending_payment": ICON.pending_payment,
    "paid": ICON.paid,
    "delivered": ICON.delivered,
    "cancelled": ICON.cancelled,
    "expired": ICON.expired,
    "refunded": ICON.refunded,
}


def delivery_icon(kind: str) -> str:
    return DELIVERY_ICONS.get(kind, ICON.text)


def status_icon(status: str) -> str:
    return STATUS_ICONS.get(status, ICON.info)


def _esc(value: object) -> str:
    return html.escape(str(value))


# --- screens ----------------------------------------------------------------------------------


def screen(
    icon: str,
    title: str,
    body: str = "",
    *,
    crumbs: Sequence[str] | None = None,
    step: tuple[int, int] | tuple[int, int, str] | None = None,
) -> str:
    """Render a screen's text. title / crumbs / step are plain text, escaped
    here; body is already-rendered HTML and is left exactly as given."""
    lines = [f"{icon} <b>{_esc(title)}</b>"]
    if crumbs:
        lines.append(CRUMB_SEP.join(_esc(crumb) for crumb in crumbs))
    if step is not None:
        label = f"Step {step[0]} of {step[1]}"
        if len(step) == 3:
            label += f"{SEP_META}{step[2]}"
        lines.append(_esc(label))
    head = "\n".join(lines)
    return f"{head}\n\n{body}" if body else head


# --- keyboards --------------------------------------------------------------------------------


def nav_row(back: str | None = None) -> list[InlineKeyboardButton]:
    """The last row of every inline keyboard: "‹ Back" (omitted when `back`,
    a callback_data string, is None) and "🏠 Home"."""
    from .i18n import t  # late import: i18n imports this module for ICON

    row: list[InlineKeyboardButton] = []
    if back is not None:
        row.append(InlineKeyboardButton(t("nav.back"), callback_data=back))
    row.append(InlineKeyboardButton(t("nav.home"), callback_data=HOME_CALLBACK))
    return row


def btn_label(text: str, max_len: int = 40) -> str:
    """A button label of at most max_len characters, ending in "…" when cut.
    Never empty."""
    text = " ".join(str(text).split())
    if not text:
        return ELLIPSIS
    if len(text) <= max_len:
        return text
    return text[: max(max_len - 1, 1)].rstrip() + ELLIPSIS


def trim(text: str, budget: int) -> str:
    """Shorten RAW (unescaped) text to at most `budget` characters, ending in
    "…" when cut. Call it BEFORE escaping: cutting escaped text could split an
    entity like "&amp;" or a tag."""
    if budget < 1:
        return ""
    if len(text) <= budget:
        return text
    return text[: budget - 1].rstrip() + ELLIPSIS


def trim_escaped(text: str, budget: int) -> str:
    """Like trim(), but the budget is the length AFTER escaping: text full of
    "&" or "<" grows fivefold or fourfold when rendered, so a raw-length budget
    cannot promise a message stays under Telegram's limit. Returns RAW text
    (still to be escaped by the caller), ending in "…" when cut."""
    if len(html.escape(text)) <= budget:
        return text
    kept: list[str] = []
    size = len(ELLIPSIS)
    for char in text:
        width = len(html.escape(char))
        if size + width > budget:
            break
        kept.append(char)
        size += width
    return "".join(kept).rstrip() + ELLIPSIS


def fit_lines(lines: Sequence[str], budget: int) -> tuple[list[str], int]:
    """The longest prefix of already-rendered `lines` whose joined length (one
    newline between lines) is within `budget`, but never fewer than one line.
    Returns (shown lines, how many were left out)."""
    shown: list[str] = []
    size = 0
    for line in lines:
        size += len(line) + (1 if shown else 0)
        if shown and size > budget:
            break
        shown.append(line)
    return shown, len(lines) - len(shown)


def pager(page: int, pages: int, callback_for: Callable[[int], str]) -> list[InlineKeyboardButton]:
    """One pager row: "◀", the "2 / 5" indicator and "▶". The arrows are
    omitted at the boundaries; with a single page there is no pager (an empty
    row). `page` is zero-based; callback_for(n) gives the callback_data for
    page n."""
    if pages <= 1:
        return []
    page = max(0, min(page, pages - 1))
    row: list[InlineKeyboardButton] = []
    if page > 0:
        row.append(InlineKeyboardButton(ICON.prev, callback_data=callback_for(page - 1)))
    row.append(InlineKeyboardButton(f"{page + 1} / {pages}", callback_data=NOOP_CALLBACK))
    if page < pages - 1:
        row.append(InlineKeyboardButton(ICON.next, callback_data=callback_for(page + 1)))
    return row
