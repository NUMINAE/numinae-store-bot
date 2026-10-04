"""The bot's Telegram profile: command menus, descriptions, and the owner-scope retry."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram import BotCommandScopeChat, BotCommandScopeDefault
from telegram.error import BadRequest, NetworkError

from bot import main, profile
from bot.handlers import common
from bot.profile import (
    ADMIN_COMMAND_NAMES,
    COMMAND_NAME_PATTERN,
    CUSTOMER_COMMAND_NAMES,
    MAX_COMMAND_DESCRIPTION,
    MAX_DESCRIPTION,
    MAX_SHORT_DESCRIPTION,
    MIN_COMMAND_DESCRIPTION,
    describe_profile,
    maybe_retry_owner_scope,
    sync_profile,
)

from .conftest import OWNER_ID
from .helpers import make_context, make_message, make_update

STRANGER = 4242
TOKEN = "123456789:AAH" + "x" * 32  # shaped like a real bot token (35 characters after the colon)


@pytest.fixture(autouse=True)
def fresh_state():
    profile.reset_state()
    yield
    profile.reset_state()


def fake_bot(**overrides) -> SimpleNamespace:
    bot = SimpleNamespace(
        set_my_commands=AsyncMock(),
        set_my_description=AsyncMock(),
        set_my_short_description=AsyncMock(),
        send_message=AsyncMock(),
    )
    for name, value in overrides.items():
        setattr(bot, name, value)
    return bot


def scopes(bot) -> list:
    return [c.kwargs.get("scope") for c in bot.set_my_commands.await_args_list]


def commands_for(bot, scope_type, chat_id=None) -> list[str]:
    for call in bot.set_my_commands.await_args_list:
        scope = call.kwargs["scope"]
        if isinstance(scope, scope_type) and (
            chat_id is None or getattr(scope, "chat_id", None) == chat_id
        ):
            return [c.command for c in call.args[0]]
    raise AssertionError(f"no set_my_commands call for {scope_type.__name__}")


# --- the profile as data ----------------------------------------------------------------------


def test_command_names_match_telegrams_pattern():
    description = describe_profile()
    for command in (*description.default_commands, *description.owner_commands):
        assert COMMAND_NAME_PATTERN.match(command.command), command.command
    assert COMMAND_NAME_PATTERN.pattern == "^[a-z0-9_]{1,32}$"


def test_command_descriptions_are_within_telegrams_length_limits():
    description = describe_profile()
    for command in (*description.default_commands, *description.owner_commands):
        assert MIN_COMMAND_DESCRIPTION <= len(command.description) <= MAX_COMMAND_DESCRIPTION


def test_the_descriptions_fit_telegrams_limits():
    description = describe_profile()
    assert 0 < len(description.description) <= MAX_DESCRIPTION
    assert 0 < len(description.short_description) <= MAX_SHORT_DESCRIPTION
    assert (MAX_COMMAND_DESCRIPTION, MAX_DESCRIPTION, MAX_SHORT_DESCRIPTION) == (256, 512, 120)


def test_the_default_commands_are_customer_commands_only():
    description = describe_profile()
    assert tuple(c.command for c in description.default_commands) == CUSTOMER_COMMAND_NAMES
    assert not {c.command for c in description.default_commands} & set(ADMIN_COMMAND_NAMES)


def test_the_owner_commands_are_the_customer_commands_plus_admin_ones():
    description = describe_profile()
    assert tuple(c.command for c in description.owner_commands) == (
        *CUSTOMER_COMMAND_NAMES,
        *ADMIN_COMMAND_NAMES,
    )


# --- syncing ----------------------------------------------------------------------------------


async def test_the_default_command_scope_has_no_admin_commands(db):
    bot = fake_bot()
    results = await sync_profile(bot, OWNER_ID)

    default = commands_for(bot, BotCommandScopeDefault)
    assert default == list(CUSTOMER_COMMAND_NAMES)
    assert not set(default) & set(ADMIN_COMMAND_NAMES)
    assert results == {
        "default_commands": True,
        "owner_commands": True,
        "description": True,
        "short_description": True,
    }


async def test_the_owner_scope_adds_the_admin_commands_for_the_owners_chat_only(db):
    bot = fake_bot()
    await sync_profile(bot, OWNER_ID)
    owner_scope = commands_for(bot, BotCommandScopeChat, chat_id=OWNER_ID)
    assert owner_scope == [*CUSTOMER_COMMAND_NAMES, *ADMIN_COMMAND_NAMES]
    assert len(scopes(bot)) == 2  # default + the owner's chat, nothing else


async def test_the_descriptions_are_pushed(db):
    bot = fake_bot()
    await sync_profile(bot, OWNER_ID)
    description = describe_profile()
    bot.set_my_description.assert_awaited_once_with(description.description)
    bot.set_my_short_description.assert_awaited_once_with(description.short_description)


async def test_a_failing_bot_never_raises_and_every_part_is_attempted(db, caplog):
    caplog.set_level(logging.WARNING)
    boom = AsyncMock(
        side_effect=NetworkError(f"cannot reach https://api.telegram.org/bot{TOKEN}/x")
    )
    bot = fake_bot(set_my_commands=boom, set_my_description=boom, set_my_short_description=boom)

    results = await sync_profile(bot, OWNER_ID)

    assert results == {
        "default_commands": False,
        "owner_commands": False,
        "description": False,
        "short_description": False,
    }
    assert boom.await_count == 4  # one failure did not stop the next call
    assert TOKEN not in caplog.text
    assert all(r.levelno == logging.WARNING for r in caplog.records)
    assert not any(r.exc_info for r in caplog.records)  # message only, no traceback


async def test_a_profile_sync_failure_does_not_block_startup(db, caplog):
    caplog.set_level(logging.WARNING)
    boom = AsyncMock(side_effect=RuntimeError(f"api down, token {TOKEN}"))
    application = SimpleNamespace(
        bot=fake_bot(set_my_commands=boom, set_my_description=boom, set_my_short_description=boom)
    )
    await main._post_init(application)  # returns normally: the bot goes on to start polling
    assert TOKEN not in caplog.text


async def test_post_init_survives_even_a_crash_inside_sync_profile(db, monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    monkeypatch.setattr(main.profile, "sync_profile", AsyncMock(side_effect=RuntimeError(TOKEN)))
    await main._post_init(SimpleNamespace(bot=fake_bot()))
    assert TOKEN not in caplog.text
    assert any("Profile sync failed" in r.getMessage() for r in caplog.records)


def test_post_init_is_registered_on_the_application(db):
    assert main.build_application().post_init is main._post_init


async def test_a_failing_owner_scope_logs_a_hint_to_send_start(db, caplog):
    caplog.set_level(logging.WARNING)

    async def fail_owner_only(commands, scope=None, **kwargs):
        if isinstance(scope, BotCommandScopeChat):
            raise BadRequest("Chat not found")

    bot = fake_bot(set_my_commands=AsyncMock(side_effect=fail_owner_only))
    results = await sync_profile(bot, OWNER_ID)

    assert results["default_commands"] is True and results["owner_commands"] is False
    assert any("/start" in r.getMessage() for r in caplog.records)


# --- the owner retry --------------------------------------------------------------------------


def owner_scope_flaky_bot() -> SimpleNamespace:
    """Fails the owner's chat scope the first time, succeeds ever after."""
    state = {"owner_calls": 0}

    async def set_my_commands(commands, scope=None, **kwargs):
        if isinstance(scope, BotCommandScopeChat):
            state["owner_calls"] += 1
            if state["owner_calls"] == 1:
                raise BadRequest("Chat not found")

    return fake_bot(set_my_commands=AsyncMock(side_effect=set_my_commands))


def owner_calls(bot) -> int:
    return sum(isinstance(s, BotCommandScopeChat) for s in scopes(bot))


async def start_as(user_id: int, bot) -> None:
    context = make_context()
    context.bot = bot
    await common.start(
        make_update(user_id, message=make_message("/start"), chat_id=user_id), context
    )


async def test_the_owner_scope_is_retried_after_the_owners_first_start(db):
    bot = owner_scope_flaky_bot()
    await sync_profile(bot, OWNER_ID)
    assert owner_calls(bot) == 1  # failed at startup

    await start_as(OWNER_ID, bot)

    assert owner_calls(bot) == 2  # the retry
    assert commands_for(bot, BotCommandScopeChat, OWNER_ID) == [
        *CUSTOMER_COMMAND_NAMES,
        *ADMIN_COMMAND_NAMES,
    ]
    assert profile._state.owner_scope_ok is True


async def test_a_non_owner_start_never_triggers_a_sync(db):
    bot = owner_scope_flaky_bot()
    await sync_profile(bot, OWNER_ID)
    before = bot.set_my_commands.await_count

    await start_as(STRANGER, bot)
    await start_as(STRANGER + 1, bot)

    assert bot.set_my_commands.await_count == before
    assert owner_calls(bot) == 1


async def test_the_retry_happens_only_once_even_if_it_fails_again(db):
    async def always_fails(commands, scope=None, **kwargs):
        if isinstance(scope, BotCommandScopeChat):
            raise BadRequest("Chat not found")

    bot = fake_bot(set_my_commands=AsyncMock(side_effect=always_fails))
    await sync_profile(bot, OWNER_ID)
    assert owner_calls(bot) == 1

    await start_as(OWNER_ID, bot)
    await start_as(OWNER_ID, bot)
    await start_as(OWNER_ID, bot)

    assert owner_calls(bot) == 2  # startup + exactly one retry


async def test_no_retry_when_the_owner_scope_already_succeeded(db):
    bot = fake_bot()
    await sync_profile(bot, OWNER_ID)
    assert await maybe_retry_owner_scope(bot, OWNER_ID) is False
    assert owner_calls(bot) == 1


async def test_a_retry_that_raises_cannot_break_start(db):
    bot = fake_bot(set_my_commands=AsyncMock(side_effect=RuntimeError("down")))
    await start_as(OWNER_ID, bot)  # start() still answers the owner
    assert bot.set_my_commands.await_count == 1
