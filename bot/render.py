"""HTML escaping and formatting helpers. Every user-supplied string must pass
through esc() at render time. Nothing else in the codebase should build HTML
strings directly — store raw values in the database, escape only on output.
"""

from __future__ import annotations

import html
from typing import Any


def esc(value: Any) -> str:
    return html.escape(str(value))


def stars(amount: int) -> str:
    """Money is whole Telegram Stars; this is the only place it is formatted."""
    return f"⭐ {amount}"


def code(value: Any) -> str:
    return f"<code>{esc(value)}</code>"


def bold(value: Any) -> str:
    return f"<b>{esc(value)}</b>"


def strike(value: Any) -> str:
    return f"<s>{esc(value)}</s>"


def pre(value: Any) -> str:
    return f"<pre>{esc(value)}</pre>"
