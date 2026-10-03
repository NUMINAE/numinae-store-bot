from __future__ import annotations

import logging

import pytest
from telegram.constants import ParseMode
from telegram.error import BadRequest

from bot.ui import show_screen

from .helpers import empty_keyboard, make_callback_query, make_context, make_message, make_update

CHAT_ID = 5000


def _setup(edit_error: Exception | None = None, delete_error: Exception | None = None):
    message = make_message()
    message.edit_text.side_effect = edit_error
    message.delete.side_effect = delete_error
    query = make_callback_query(message=message)
    update = make_update(1001, callback_query=query, chat_id=CHAT_ID)
    return update, make_context(), message, query


async def test_edits_the_existing_message_in_place():
    update, context, message, query = _setup()
    keyboard = empty_keyboard()
    await show_screen(update, context, text="hello", keyboard=keyboard)
    message.edit_text.assert_awaited_once_with(
        "hello", parse_mode=ParseMode.HTML, reply_markup=keyboard
    )
    message.delete.assert_not_called()
    context.bot.send_message.assert_not_called()
    query.answer.assert_awaited_once_with()


async def test_alert_is_shown_as_a_popup():
    update, context, _, query = _setup()
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
async def test_message_not_modified_is_swallowed(text, caplog):
    caplog.set_level(logging.WARNING)
    update, context, message, _ = _setup(edit_error=BadRequest(text))
    await show_screen(update, context, text="same", keyboard=empty_keyboard())
    message.delete.assert_not_called()
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
async def test_other_bad_requests_fall_back_to_delete_and_resend(text, caplog):
    caplog.set_level(logging.WARNING)
    update, context, message, _ = _setup(edit_error=BadRequest(text))
    keyboard = empty_keyboard()
    await show_screen(update, context, text="fresh", keyboard=keyboard)
    message.delete.assert_awaited_once()
    context.bot.send_message.assert_awaited_once_with(
        chat_id=CHAT_ID, text="fresh", parse_mode=ParseMode.HTML, reply_markup=keyboard
    )
    assert any("falling back to resend" in r.getMessage() for r in caplog.records)


async def test_fallback_still_resends_when_the_delete_also_fails():
    update, context, message, _ = _setup(
        edit_error=BadRequest("Message can't be edited"),
        delete_error=BadRequest("Message can't be deleted"),
    )
    await show_screen(update, context, text="fresh", keyboard=empty_keyboard())
    message.delete.assert_awaited_once()
    context.bot.send_message.assert_awaited_once()


async def test_without_a_callback_query_a_new_message_is_sent():
    update = make_update(1001, message=make_message("/start"), chat_id=CHAT_ID)
    context = make_context()
    keyboard = empty_keyboard()
    await show_screen(update, context, text="menu", keyboard=keyboard)
    context.bot.send_message.assert_awaited_once_with(
        chat_id=CHAT_ID, text="menu", parse_mode=ParseMode.HTML, reply_markup=keyboard
    )


async def test_non_badrequest_errors_propagate():
    update, context, _, _ = _setup(edit_error=RuntimeError("network down"))
    with pytest.raises(RuntimeError):
        await show_screen(update, context, text="x", keyboard=empty_keyboard())
