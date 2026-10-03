"""Shared fixtures. The environment must be set before anything imports
bot.config, which validates it at import time (and would otherwise pick up a
developer's real .env)."""

from __future__ import annotations

import dataclasses
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

from bot import db as db_module  # noqa: E402
from bot import ratelimit  # noqa: E402


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """A fresh, seeded temporary database behind the module-level connection."""
    db_module.close_conn()
    config = dataclasses.replace(db_module.CONFIG, db_path=str(tmp_path / "test.db"))
    monkeypatch.setattr(db_module, "CONFIG", config)
    db_module.init_db()
    yield db_module.get_conn()
    db_module.close_conn()


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
