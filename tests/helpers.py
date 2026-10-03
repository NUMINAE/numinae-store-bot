"""Minimal fakes for Telegram objects. The code under test only touches a
handful of attributes, so plain namespaces with AsyncMock methods are enough."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from telegram import InlineKeyboardMarkup


def make_user(user_id: int, first_name: str | None = "Test", username: str | None = None) -> Any:
    return SimpleNamespace(id=user_id, first_name=first_name, username=username)


def make_message(text: str | None = None, message_id: int = 1) -> Any:
    return SimpleNamespace(
        message_id=message_id,
        text=text,
        reply_text=AsyncMock(),
        edit_text=AsyncMock(),
        delete=AsyncMock(),
    )


def make_callback_query(data: str = "x", message: Any = None) -> Any:
    return SimpleNamespace(
        data=data,
        answer=AsyncMock(),
        message=message if message is not None else make_message(),
    )


def make_update(
    user_id: int | None = 1001,
    *,
    callback_query: Any = None,
    message: Any = None,
    chat_id: int | None = 5000,
    user: Any = None,
) -> Any:
    if user is None and user_id is not None:
        user = make_user(user_id)
    return SimpleNamespace(
        effective_user=user,
        effective_chat=SimpleNamespace(id=chat_id) if chat_id is not None else None,
        callback_query=callback_query,
        message=message,
    )


def make_context() -> Any:
    return SimpleNamespace(
        bot=SimpleNamespace(send_message=AsyncMock()),
        job_queue=MagicMock(),
        user_data={},
    )


def empty_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([])


def add_customer(user_id: int, username: str | None = None) -> int:
    from bot.repo import customers as customers_repo

    customers_repo.upsert(user_id, username, f"User {user_id}")
    return user_id
