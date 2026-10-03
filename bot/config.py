"""Environment loading and validation. Import this module to get a ready-to-use
CONFIG; it fails loudly at import time if required variables are missing or malformed."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


class ConfigError(Exception):
    """Raised for a missing/malformed required env var."""


@dataclass(frozen=True)
class Config:
    bot_token: str
    owner_id: int
    db_path: str
    log_level: str


def _require_bot_token() -> str:
    value = os.environ.get("BOT_TOKEN", "").strip()
    if not value:
        raise ConfigError(
            "BOT_TOKEN is missing. Set it in your .env file — get a token from "
            "@BotFather on Telegram."
        )
    return value


def _require_owner_id() -> int:
    raw = os.environ.get("OWNER_ID", "").strip()
    if not raw:
        raise ConfigError(
            "OWNER_ID is missing. Set it in your .env file to your numeric "
            "Telegram user id (see README for how to find it)."
        )
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(
            f"OWNER_ID is malformed: {raw!r} is not an integer. It must be your "
            "numeric Telegram user id."
        ) from None


def _load() -> Config:
    bot_token = _require_bot_token()
    owner_id = _require_owner_id()
    db_path = os.environ.get("DB_PATH", "data/shop.db").strip() or "data/shop.db"
    log_level = os.environ.get("LOG_LEVEL", "INFO").strip().upper() or "INFO"
    return Config(bot_token=bot_token, owner_id=owner_id, db_path=db_path, log_level=log_level)


try:
    CONFIG = _load()
except ConfigError as exc:
    sys.exit(f"Configuration error: {exc}")
