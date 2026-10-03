"""Keeping the bot token out of logs and messages. Telegram request URLs embed
the token (https://api.telegram.org/bot<token>/method) and HTTP exception text
can quote them, so every log line and every error detail passes through
redact()."""

from __future__ import annotations

import logging
import re

# A Telegram bot token: an 8-10 digit bot id, a colon, 35 URL-safe characters.
# Plain \b boundaries would miss the token inside a URL, where it directly
# follows the letters "bot" (no word boundary between "t" and a digit), so the
# edges are lookarounds instead.
_TOKEN = re.compile(r"(?<!\d)\d{8,10}:[A-Za-z0-9_-]{35}(?![A-Za-z0-9_-])")

REDACTED = "[redacted]"


def redact(text: str) -> str:
    """Replace anything shaped like a bot token with "[redacted]"."""
    return _TOKEN.sub(REDACTED, text)


class RedactingFormatter(logging.Formatter):
    """A formatter that redacts the FULL formatted output: the message after its
    args are applied, and any traceback."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))
