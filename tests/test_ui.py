"""show_screen against real telegram.Message / InaccessibleMessage objects (the
isinstance checks in bot/ui.py need the real types). Their edit_text / delete
methods are replaced on the class with AsyncMocks."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram import Chat, InaccessibleMessage, Message
from telegram.constants import ParseMode
from telegram.error import BadRequest

from bot.ui import show_screen

from .helpers import empty_keyboard, make_callback_query, make_context, make_message, make_update

CHAT_ID = 5000


@pytest.fixture
def edit(monkeypatch) -> AsyncMock:
    mock = AsyncMock()
    monkeypatch.setattr(Message, "edit_text", mock)
    return mock


@pytest.fixture
def delete(monkeypatch) -> AsyncMock:
    mock = AsyncMock()
    monkeypatch.setattr(Message, "delete", mock)
    return mock


def real_message(age: timedelta = timedelta(minutes=1), chat_id: int = CHAT_ID) -> Message:
    return Message(
        message_id=7,
        date=datetime.now(UTC) - age,
        chat=Chat(id=chat_id, type="private"),
    )


def _setup(message=None, chat_id: int | None = CHAT_ID):
    message = message if message is not None else real_message()
    query = make_callback_query(message=message)
    update = make_update(1001, callback_query=query, chat_id=chat_id)
    return update, make_context(), query


def _sent_fresh(context, keyboard, text):
    context.bot.send_message.assert_awaited_once_with(
        chat_id=CHAT_ID, text=text, parse_mode=ParseMode.HTML, reply_markup=keyboard
    )


async def test_edits_the_existing_message_in_place(edit, delete):
    update, context, query = _setup()
    keyboard = empty_keyboard()
    await show_screen(update, context, text="hello", keyboard=keyboard)
    edit.assert_awaited_once_with("hello", parse_mode=ParseMode.HTML, reply_markup=keyboard)
    delete.assert_not_called()
    context.bot.send_message.assert_not_called()
    query.answer.assert_awaited_once_with()


async def test_alert_is_shown_as_a_popup(edit):
    update, context, query = _setup()
    await show_screen(update, context, text="t", keyboard=empty_keyboard(), alert="careful")
    query.answer.assert_awaited_once_with("careful", show_alert=True)


@pytest.mark.parametrize(
    "text",
    [
        "Bad Request: message is not modified: specified new message content and reply markup "
        "are exactly the same",
        "MESSAGE IS NOT MODIFIED",
    ],
)
async def test_message_not_modified_is_swallowed(edit, delete, text, caplog):
    caplog.set_level(logging.WARNING)
    edit.side_effect = BadRequest(text)
    update, context, _ = _setup()
    await show_screen(update, context, text="same", keyboard=empty_keyboard())
    delete.assert_not_called()
    context.bot.send_message.assert_not_called()
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


@pytest.mark.parametrize(
    "text",
    [
        "Message can't be edited",
        "There is no text in the message to edit",
        "Message to edit not found",
    ],
)
async def test_other_bad_requests_fall_back_to_delete_and_resend(edit, delete, text, caplog):
    caplog.set_level(logging.WARNING)
    edit.side_effect = BadRequest(text)
    update, context, _ = _setup()
    keyboard = empty_keyboard()
    await show_screen(update, context, text="fresh", keyboard=keyboard)
    delete.assert_awaited_once()
    _sent_fresh(context, keyboard, "fresh")
    assert any("falling back to resend" in r.getMessage() for r in caplog.records)


async def test_fallback_still_resends_when_the_delete_also_fails(edit, delete):
    edit.side_effect = BadRequest("Message can't be edited")
    delete.side_effect = BadRequest("Message can't be deleted")
    update, context, _ = _setup()
    await show_screen(update, context, text="fresh", keyboard=empty_keyboard())
    delete.assert_awaited_once()
    context.bot.send_message.assert_awaited_once()


async def test_non_badrequest_errors_propagate(edit):
    edit.side_effect = RuntimeError("network down")
    update, context, _ = _setup()
    with pytest.raises(RuntimeError):
        await show_screen(update, context, text="x", keyboard=empty_keyboard())


async def test_without_a_callback_query_a_new_message_is_sent(edit):
    update = make_update(1001, message=make_message("/start"), chat_id=CHAT_ID)
    context = make_context()
    keyboard = empty_keyboard()
    await show_screen(update, context, text="menu", keyboard=keyboard)
    _sent_fresh(context, keyboard, "menu")
    edit.assert_not_called()


# --- inaccessible / old messages / missing chat -----------------------------


async def test_inaccessible_message_gets_a_fresh_message_and_no_edit_or_delete(edit, delete):
    gone = InaccessibleMessage(chat=Chat(id=CHAT_ID, type="private"), message_id=7)
    update, context, query = _setup(message=gone)
    keyboard = empty_keyboard()
    await show_screen(update, context, text="fresh", keyboard=keyboard)
    edit.assert_not_called()
    delete.assert_not_called()
    _sent_fresh(context, keyboard, "fresh")
    query.answer.assert_awaited_once_with()


@pytest.mark.parametrize("hours", [48, 49, 24 * 30])
async def test_messages_older_than_48_hours_are_not_edited_or_deleted(edit, delete, hours):
    update, context, _ = _setup(message=real_message(age=timedelta(hours=hours, seconds=1)))
    keyboard = empty_keyboard()
    await show_screen(update, context, text="fresh", keyboard=keyboard)
    edit.assert_not_called()
    delete.assert_not_called()
    _sent_fresh(context, keyboard, "fresh")


async def test_a_message_just_inside_the_window_is_still_edited(edit):
    update, context, _ = _setup(message=real_message(age=timedelta(hours=47, minutes=59)))
    await show_screen(update, context, text="fresh", keyboard=empty_keyboard())
    edit.assert_awaited_once()
    context.bot.send_message.assert_not_called()


async def test_missing_effective_chat_falls_back_to_the_message_chat(edit):
    gone = InaccessibleMessage(chat=Chat(id=CHAT_ID, type="private"), message_id=7)
    update, context, _ = _setup(message=gone, chat_id=None)
    keyboard = empty_keyboard()
    await show_screen(update, context, text="fresh", keyboard=keyboard)
    _sent_fresh(context, keyboard, "fresh")


async def test_no_chat_anywhere_sends_nothing_and_does_not_crash(caplog):
    caplog.set_level(logging.WARNING)
    update = SimpleNamespace(effective_chat=None, callback_query=None, effective_user=None)
    context = make_context()
    await show_screen(update, context, text="x", keyboard=empty_keyboard())
    context.bot.send_message.assert_not_called()
    assert any("no chat" in r.getMessage() for r in caplog.records)
