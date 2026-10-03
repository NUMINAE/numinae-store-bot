"""Environment-backed settings. get_settings() is cached; tests clear it with
get_settings.cache_clear(). On a missing or invalid variable the process exits
with a message naming each offending variable and never echoing any value —
the bot token must not reach a log or a terminal."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    bot_token: SecretStr = Field(min_length=1)
    owner_id: int
    db_path: Path = Path("data/shop.db")
    log_level: LogLevel = "INFO"
    payment_mode: Literal["stars", "mock"] = "mock"
    seed_demo: bool = True

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalise_log_level(cls, value: Any) -> Any:
        return value.strip().upper() if isinstance(value, str) else value


def describe_errors(exc: ValidationError) -> str:
    """One line per offending variable. Only the variable name and the error
    category are used — pydantic's own messages can quote the input."""
    lines = []
    for error in exc.errors():
        name = str(error["loc"][0]).upper() if error["loc"] else "SETTINGS"
        problem = "is missing" if error["type"] == "missing" else f"is invalid ({error['type']})"
        lines.append(f"  {name} {problem}")
    return "Configuration error. Fix these environment variables (see .env.example):\n" + "\n".join(
        lines
    )


def load_settings(env_file: str | None = ".env") -> Settings:
    try:
        return Settings(_env_file=env_file)  # type: ignore[call-arg]  # pydantic-settings init kwarg
    except ValidationError as exc:
        raise SystemExit(describe_errors(exc)) from None


@lru_cache
def get_settings() -> Settings:
    return load_settings()
