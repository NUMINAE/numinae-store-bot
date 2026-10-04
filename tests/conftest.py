"""Shared fixtures. The environment must be set before anything reads
settings, and the settings cache is cleared around every database fixture so
a test's DB_PATH / SEED_DEMO never leaks into the next one (and a developer's
real .env never overrides them: real environment variables win over .env)."""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

OWNER_ID = 1
os.environ["BOT_TOKEN"] = "test-token-not-real"
os.environ["OWNER_ID"] = str(OWNER_ID)
os.environ["LOG_LEVEL"] = "WARNING"

from bot import db as db_module  # noqa: E402 - env vars must be set before bot is imported
from bot import ratelimit  # noqa: E402 - env vars must be set before bot is imported
from bot.config import get_settings  # noqa: E402 - env vars must be set before bot is imported
from bot.seed_demo import seed_demo  # noqa: E402 - env vars must be set before bot is imported


def _open_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, demo: bool) -> Any:
    db_module.close_conn()
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("SEED_DEMO", "true" if demo else "false")
    get_settings.cache_clear()
    db_module.init_db()
    seed_demo()
    return db_module.get_conn()


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """A fresh temporary database with the demo catalogue behind the
    module-level connection."""
    yield _open_db(tmp_path, monkeypatch, demo=True)
    db_module.close_conn()
    get_settings.cache_clear()


@pytest.fixture
def empty_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """Migrated but unseeded: only the settings defaults from migration 0001."""
    yield _open_db(tmp_path, monkeypatch, demo=False)
    db_module.close_conn()
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def fresh_owner_notify() -> Iterator[None]:
    """owner_notify keeps its rate-limit window in module state; every test starts
    with no window open, whatever earlier tests did."""
    from bot import owner_notify

    def reset() -> None:
        owner_notify._window_open = False
        owner_notify._sent_count = 0
        owner_notify._suppressed_count = 0

    reset()
    yield
    reset()


@pytest.fixture
def clean_ratelimit() -> Iterator[None]:
    """The throttle keeps module-level state; isolate each test from it."""
    ratelimit._hits.clear()
    ratelimit._warned_until.clear()
    ratelimit._calls_since_gc = 0
    yield
    ratelimit._hits.clear()
    ratelimit._warned_until.clear()
    ratelimit._calls_since_gc = 0


@pytest.fixture
def warnings_log(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.WARNING)
    return caplog
