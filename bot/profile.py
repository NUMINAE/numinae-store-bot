"""The bot's Telegram profile: command menus, description and short description.

describe_profile() is a pure function (easy to test against the limits);
sync_profile() pushes it to Telegram. Syncing is best-effort: every Telegram
call is individually guarded, a failure logs a WARNING (message only, never a
traceback or a secret) and nothing here can block startup.

Command scopes: the DEFAULT scope (everyone) lists only the customer commands.
The owner's chat scope adds admin, order and sweep. The owner-scope call can
fail simply because the owner has never opened the chat with the bot; it is
retried once, in memory, the first time the owner sends /start."""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from telegram import Bot, BotCommand, BotCommandScopeChat, BotCommandScopeDefault
from telegram.constants import BotCommandLimit, BotDescriptionLimit

from .config import get_settings
from .i18n import t
from .redact import redact

logger = logging.getLogger(__name__)

COMMAND_NAME_PATTERN = re.compile(r"^[a-z0-9_]{1,32}$")
MAX_COMMAND_NAME = BotCommandLimit.MAX_COMMAND.value
MIN_COMMAND_DESCRIPTION = BotCommandLimit.MIN_COMMAND.value
MAX_COMMAND_DESCRIPTION = BotCommandLimit.MAX_DESCRIPTION.value  # 256
MAX_DESCRIPTION = BotDescriptionLimit.MAX_DESCRIPTION_LENGTH  # 512
MAX_SHORT_DESCRIPTION = BotDescriptionLimit.MAX_SHORT_DESCRIPTION_LENGTH  # 120

CUSTOMER_COMMAND_NAMES = ("start", "library", "paysupport")
ADMIN_COMMAND_NAMES = ("admin", "order", "sweep")


@dataclass(frozen=True)
class Profile:
    default_commands: tuple[BotCommand, ...]  # shown to everyone
    owner_commands: tuple[BotCommand, ...]  # shown only in the owner's chat
    description: str
    short_description: str


def describe_profile() -> Profile:
    """The profile as data. No network, no settings."""
    customer = tuple(BotCommand(name, t(f"profile.cmd.{name}")) for name in CUSTOMER_COMMAND_NAMES)
    admin = tuple(BotCommand(name, t(f"profile.cmd.{name}")) for name in ADMIN_COMMAND_NAMES)
    return Profile(
        default_commands=customer,
        owner_commands=customer + admin,
        description=t("profile.description"),
        short_description=t("profile.short_description"),
    )


@dataclass
class _State:
    owner_scope_ok: bool = False
    owner_retry_done: bool = False


_state = _State()


def reset_state() -> None:
    """For tests: forget what this process has synced."""
    _state.owner_scope_ok = False
    _state.owner_retry_done = False


async def _guarded(label: str, call: Callable[[], Awaitable[object]]) -> bool:
    try:
        await call()
    except Exception as exc:
        logger.warning("Profile sync: %s failed: %s", label, redact(str(exc)))
        return False
    return True


async def _sync_owner_scope(bot: Bot, owner_id: int, profile: Profile) -> bool:
    ok = await _guarded(
        "owner command scope",
        lambda: bot.set_my_commands(profile.owner_commands, scope=BotCommandScopeChat(owner_id)),
    )
    if not ok:
        logger.warning(
            "Profile sync: the owner's command menu was not set. If the owner has never "
            "opened this chat, send /start to the bot once; it will be retried then."
        )
    _state.owner_scope_ok = ok
    return ok


async def sync_profile(bot: Bot, owner_id: int) -> dict[str, bool]:
    """Push the profile to Telegram. Returns which parts succeeded; never raises."""
    results: dict[str, bool] = {}
    try:
        profile = describe_profile()
        results["default_commands"] = await _guarded(
            "default command scope",
            lambda: bot.set_my_commands(profile.default_commands, scope=BotCommandScopeDefault()),
        )
        results["owner_commands"] = await _sync_owner_scope(bot, owner_id, profile)
        results["description"] = await _guarded(
            "description", lambda: bot.set_my_description(profile.description)
        )
        results["short_description"] = await _guarded(
            "short description", lambda: bot.set_my_short_description(profile.short_description)
        )
    except Exception as exc:  # belt and braces: building the profile itself failed
        logger.warning("Profile sync aborted: %s", redact(str(exc)))
    return results


async def maybe_retry_owner_scope(bot: Bot, user_id: int) -> bool:
    """Called on every /start. Only the owner's FIRST /start in this process can
    trigger the retry, and only if the owner scope is not already set; anyone
    else's /start never syncs anything. Returns whether a retry was attempted."""
    owner_id = get_settings().owner_id
    if user_id != owner_id or _state.owner_retry_done:
        return False
    _state.owner_retry_done = True
    if _state.owner_scope_ok:
        return False
    await _sync_owner_scope(bot, owner_id, describe_profile())
    return True
