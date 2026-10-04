"""Where the database lives, without needing the rest of the configuration.

The health check and the backup command run in places (a container healthcheck, a
cron job) where BOT_TOKEN and the other settings are neither needed nor wanted, so
they read DB_PATH alone: from the environment, then .env, then the default. The
full Settings model is deliberately not loaded."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_DB_PATH = Path("data/shop.db")


class _DbPathSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    db_path: Path = DEFAULT_DB_PATH


def resolve_db_path(override: str | Path | None = None) -> Path:
    """`override` (a command-line value) wins; otherwise DB_PATH from the
    environment or .env; otherwise data/shop.db."""
    if override:
        return Path(override)
    return _DbPathSettings().db_path
