"""A harness for whole-bot screen tests: it routes a callback tap to the handler
that owns its prefix (as bot/main.py does), checks every screen it produces
against the design language, and can crawl the bot breadth-first by tapping
every button it finds."""

from __future__ import annotations

import html
import re
from collections import deque
from dataclasses import dataclass, field
from html.parser import HTMLParser

from telegram import InlineKeyboardMarkup

from bot.handlers import admin_catalogue, cart, catalogue, checkout, library
from bot.handlers.common import help_placeholder, menu_show
from bot.ui_kit import HOME_CALLBACK, ICON, NOOP_CALLBACK

from .flow import Flow

TELEGRAM_TEXT_LIMIT = 4096
# A safety bound only: a navigation loop must never hang CI. The crawls finish far
# below it (they stop when every reachable button has been tapped).
CRAWL_BOUND = 5000
CALLBACK_BYTE_LIMIT = 64
EXPIRED_ALERT = "This menu has expired."
ALLOWED_TAGS = {"b", "code", "s", "i"}
DOUBLE_ESCAPED = ("&amp;amp;", "&amp;lt;", "&amp;gt;", "&amp;quot;", "&amp;#x27;")
HEADER = re.compile(r"^\S+ <b>.+</b>")

ROUTES = (
    ("cat:", catalogue.route),
    ("cart:", cart.route),
    ("adm:", admin_catalogue.route),
    ("co:", checkout.route),
    ("lib:", library.route),
    ("menu:", menu_show),
    ("help:", help_placeholder),
)

# Buttons that change state irreversibly (or finish something): the crawler taps
# them only after every read-only screen has been seen.
MUTATING = re.compile(
    r":yes$|^adm:(cat|pr|va):(tg|tgc|mvto|ck):|^adm:set:tgnc$|^co:(mockpay|cancel):"
    r"|^cart:(inc|dec|remove):|^lib:send:"
)


def handler_for(data: str):
    for prefix, handler in ROUTES:
        if data.startswith(prefix):
            return handler
    raise AssertionError(f"no handler owns callback_data {data!r}")


async def tap(flow: Flow, data: str) -> bool:
    """Press a button; True when it produced a new screen."""
    before = len(flow.screens)
    await flow.tap(handler_for(data), data)
    return len(flow.screens) > before


class _TagChecker(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.problems: list[str] = []

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag not in ALLOWED_TAGS:
            self.problems.append(f"unexpected tag <{tag}>")
        self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if not self.stack or self.stack[-1] != tag:
            self.problems.append(f"unbalanced </{tag}>")
        else:
            self.stack.pop()


def markup_problems(text: str) -> list[str]:
    """Telegram-HTML sanity: only known tags, all balanced, no double escaping."""
    checker = _TagChecker()
    checker.feed(text)
    checker.close()
    problems = list(checker.problems) + [f"unclosed <{tag}>" for tag in checker.stack]
    problems += [f"double-escaped {entity}" for entity in DOUBLE_ESCAPED if entity in text]
    if len(text) >= TELEGRAM_TEXT_LIMIT:
        problems.append(f"text is {len(text)} characters")
    return problems


def screen_problems(text: str, markup: InlineKeyboardMarkup, *, is_home: bool = False) -> list[str]:
    problems = markup_problems(text)
    if not HEADER.match(text):
        problems.append("does not start with an icon and a bold title")
    rows = markup.inline_keyboard
    if not rows:
        problems.append("has no buttons")
    else:
        if not is_home and not any(b.callback_data == HOME_CALLBACK for b in rows[-1]):
            problems.append("last row has no Home button")
        for row in rows:
            for button in row:
                data = button.callback_data
                if not isinstance(data, str):
                    continue
                if len(data.encode("utf-8")) > CALLBACK_BYTE_LIMIT:
                    problems.append(f"callback_data over {CALLBACK_BYTE_LIMIT} bytes: {data!r}")
                if data != NOOP_CALLBACK and ":" not in data:
                    problems.append(f"callback_data without a namespace: {data!r}")
    return problems


def sent_text_problems(flow: Flow) -> list[str]:
    """The same markup checks for every message the bot sent (not edited)."""
    problems = []
    for call in flow.context.bot.send_message.await_args_list:
        text = str(call.kwargs.get("text", ""))
        problems += [f"sent message: {p}" for p in markup_problems(text)]
    for call in flow.context.bot.send_document.await_args_list:
        caption = str(call.kwargs.get("caption", ""))
        problems += [f"document caption: {p}" for p in markup_problems(caption)]
    return problems


@dataclass
class CrawlResult:
    screens: list[tuple[str, str]] = field(default_factory=list)  # (callback_data, text)
    problems: list[str] = field(default_factory=list)
    taps: int = 0
    exhausted: bool = False  # every reachable button was tapped (not cut off by `limit`)

    @property
    def count(self) -> int:
        return len(self.screens)


async def crawl(flow: Flow, start: str, *, limit: int = CRAWL_BOUND) -> CrawlResult:
    """Breadth-first: tap `start`, then every button on every screen found, once
    per distinct callback_data. Read-only buttons first; state-changing ones last.
    Every screen is checked; a button that answers "menu expired", or does
    nothing at all, is a dead end."""
    result = CrawlResult()
    seen = {start}
    queue: deque[str] = deque([start])
    deferred: deque[str] = deque()

    while (queue or deferred) and result.count < limit:
        data = queue.popleft() if queue else deferred.popleft()
        result.taps += 1
        produced = await tap(flow, data)
        if flow.alert == EXPIRED_ALERT:
            result.problems.append(f"{data}: answered 'menu expired'")
            continue
        if not produced:
            toasts = [c for c in flow.query.answer.await_args_list if c.args]
            if flow.alert is None and not toasts:
                result.problems.append(f"{data}: no screen, no alert and no toast")
            continue
        text, markup = flow.screens[-1]
        result.screens.append((data, text))
        is_home = text.startswith(f"{ICON.home} <b>") and data.startswith("menu:")
        result.problems += [f"{data}: {p}" for p in screen_problems(text, markup, is_home=is_home)]
        for row in markup.inline_keyboard:
            for button in row:
                target = button.callback_data
                if not isinstance(target, str) or target == NOOP_CALLBACK or target in seen:
                    continue
                seen.add(target)
                (deferred if MUTATING.search(target) else queue).append(target)

    result.exhausted = not (queue or deferred)
    result.problems += sent_text_problems(flow)
    return result


def unescaped(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text))
