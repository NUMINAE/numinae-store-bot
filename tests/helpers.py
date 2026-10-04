"""Minimal fakes for Telegram objects. The code under test only touches a
handful of attributes, so plain namespaces with AsyncMock methods are enough."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from telegram import InlineKeyboardMarkup

from bot.ui_kit import ICON

HOME = f"{ICON.home} Home"  # the label of the nav row's Home button


def make_user(user_id: int, first_name: str | None = "Test", username: str | None = None) -> Any:
    return SimpleNamespace(id=user_id, first_name=first_name, username=username)


def make_message(text: str | None = None, message_id: int = 1) -> Any:
    return SimpleNamespace(
        message_id=message_id,
        text=text,
        reply_text=AsyncMock(),
        edit_text=AsyncMock(),
        delete=AsyncMock(),
        successful_payment=None,
        document=None,
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
    pre_checkout_query: Any = None,
) -> Any:
    if user is None and user_id is not None:
        user = make_user(user_id)
    return SimpleNamespace(
        effective_user=user,
        effective_chat=SimpleNamespace(id=chat_id) if chat_id is not None else None,
        callback_query=callback_query,
        message=message,
        pre_checkout_query=pre_checkout_query,
    )


def make_context() -> Any:
    """A fake PTB context. application.create_task schedules the coroutine for
    real and remembers it in `tasks`, so tests can await background work."""
    tasks: list[Any] = []

    def create_task(coroutine: Any, *args: Any, **kwargs: Any) -> Any:
        task = asyncio.ensure_future(coroutine)
        tasks.append(task)
        return task

    return SimpleNamespace(
        bot=SimpleNamespace(
            send_message=AsyncMock(),
            send_document=AsyncMock(),
            send_invoice=AsyncMock(),
            refund_star_payment=AsyncMock(),
            get_file=AsyncMock(),
        ),
        job_queue=MagicMock(),
        user_data={},
        bot_data={},
        application=SimpleNamespace(create_task=create_task),
        tasks=tasks,
    )


def empty_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([])


def add_customer(user_id: int, username: str | None = None) -> int:
    from bot.repo import customers as customers_repo

    customers_repo.upsert(user_id, username, f"User {user_id}")
    return user_id
