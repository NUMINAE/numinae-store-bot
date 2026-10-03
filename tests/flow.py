"""A tiny driver for handler flow tests: it feeds callback taps and typed
messages to the real handlers and records what the user would see. Screens are
captured at the lowest level (Message.edit_text on a real telegram.Message), so
bot/ui.py runs for real."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from telegram import Chat, InlineKeyboardMarkup, Message

from bot.handlers.payments import PROVIDER_KEY
from bot.payments.registry import build_provider

from .helpers import make_callback_query, make_context, make_message, make_update


class Flow:
    def __init__(
        self, monkeypatch: pytest.MonkeyPatch, user_id: int = 1001, mode: str = "mock"
    ) -> None:
        self.user_id = user_id
        self.edit = AsyncMock()
        monkeypatch.setattr(Message, "edit_text", self.edit)
        self.context = make_context()
        self.context.bot_data[PROVIDER_KEY] = build_provider(mode)
        self.query: Any = None
        self.screens: list[tuple[str, InlineKeyboardMarkup]] = []
        self.replies: list[str] = []

    # -- input ---------------------------------------------------------------

    async def tap(self, handler: Any, data: str) -> None:
        """Press an inline button whose callback_data is `data`."""
        message = Message(
            message_id=1, date=datetime.now(UTC), chat=Chat(id=self.user_id, type="private")
        )
        self.query = make_callback_query(data=data, message=message)
        update = make_update(self.user_id, callback_query=self.query, chat_id=self.user_id)
        before = len(self.edit.await_args_list)
        await handler(update, self.context)
        for call in self.edit.await_args_list[before:]:
            self.screens.append((call.args[0], call.kwargs["reply_markup"]))

    async def send(self, handler: Any, text: str) -> Any:
        """Send a typed message (or reply-keyboard tap)."""
        message = make_message(text)
        update = make_update(self.user_id, message=message, chat_id=self.user_id)
        await handler(update, self.context)
        self.replies += [c.args[0] for c in message.reply_text.await_args_list]
        return message

    # -- output --------------------------------------------------------------

    @property
    def text(self) -> str:
        return self.screens[-1][0]

    @property
    def buttons(self) -> list[tuple[str, str]]:
        markup = self.screens[-1][1]
        return [(b.text, str(b.callback_data)) for row in markup.inline_keyboard for b in row]

    def labels(self) -> list[str]:
        return [label for label, _ in self.buttons]

    def data_for(self, label_part: str) -> str:
        (match,) = [d for label, d in self.buttons if label_part in label]
        return match

    @property
    def alert(self) -> str | None:
        """The text of the last popup alert, if the last tap raised one."""
        for call in reversed(self.query.answer.await_args_list):
            if call.kwargs.get("show_alert"):
                return str(call.args[0])
        return None

    def sent_messages(self) -> list[Any]:
        return list(self.context.bot.send_message.await_args_list)

    async def drain(self) -> None:
        """Wait for background tasks (delivery) started by handlers."""
        while self.context.tasks:
            await self.context.tasks.pop()
