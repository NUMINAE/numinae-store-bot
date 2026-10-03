"""Typed access to the optional parts of a Telegram update. Every attribute
the handlers rely on (effective_user, callback_query, message, ...) is
Optional in python-telegram-bot's types; these helpers return the non-None
object or raise IgnoreUpdate, which the global error handler treats as a quiet
no-op. Use them instead of bare attribute access."""

from __future__ import annotations

from telegram import CallbackQuery, Message, Update, User


class IgnoreUpdate(Exception):
    """The update lacks something this handler needs; drop it silently."""


def require_user(update: Update) -> User:
    user = update.effective_user
    if user is None:
        raise IgnoreUpdate("update has no effective user")
    return user


def require_query(update: Update) -> CallbackQuery:
    query = update.callback_query
    if query is None:
        raise IgnoreUpdate("update has no callback query")
    return query


def require_message(update: Update) -> Message:
    message = update.message
    if message is None:
        raise IgnoreUpdate("update has no message")
    return message


def require_data(query: CallbackQuery) -> str:
    data = query.data
    if data is None:
        raise IgnoreUpdate("callback query has no data")
    return data


def require_text(message: Message) -> str:
    text = message.text
    if text is None:
        raise IgnoreUpdate("message has no text")
    return text
