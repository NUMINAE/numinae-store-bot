"""Liveness: a heartbeat file the sweep touches, and two command-line flags.

    python -m bot.main --check-config   validate the configuration and exit
    python -m bot.main --healthcheck    exit 0 if the heartbeat is fresh

The sweep job touches <db directory>/heartbeat at the START of every pass, so a
failing sweep still proves the event loop is alive. The flags are handled before
any network call, and neither prints a value or a secret. --healthcheck needs no
BOT_TOKEN: it reads DB_PATH only (see bot/dbpath.py)."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from .dbpath import resolve_db_path

logger = logging.getLogger(__name__)

HEARTBEAT_NAME = "heartbeat"
STALE_AFTER_SECONDS = 180  # three sweep intervals


def heartbeat_path(db_path: Path) -> Path:
    return db_path.parent / HEARTBEAT_NAME


def touch_heartbeat(path: Path | None = None) -> None:
    """Record "the event loop is alive". Never raises: a heartbeat that cannot be
    written must not stop the sweep it is reporting on."""
    try:
        if path is None:
            from .config import get_settings  # late: keeps the flags free of Settings

            path = heartbeat_path(get_settings().db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
        os.utime(path, None)
    except Exception as exc:
        logger.warning("Could not write the heartbeat: %s", type(exc).__name__)


def check_heartbeat(
    path: Path, *, now: Callable[[], float] = time.time, stale_after: float = STALE_AFTER_SECONDS
) -> tuple[bool, str]:
    """(healthy, one-line message). Healthy when the file's mtime is within
    `stale_after` seconds of now."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return False, "no heartbeat"
    age = int(now() - mtime)
    if age > stale_after:
        return False, f"stale heartbeat {age}s"
    return True, f"ok, heartbeat {max(age, 0)}s old"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m bot.main")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--check-config", action="store_true", help="validate the configuration and exit"
    )
    group.add_argument(
        "--healthcheck", action="store_true", help="exit 0 if the sweep heartbeat is fresh"
    )
    return parser


def handle_flags(argv: Sequence[str]) -> int | None:
    """Run --check-config or --healthcheck if asked. Returns the exit code, or
    None when no flag was given and the bot should start normally."""
    args = _parser().parse_args(list(argv))
    if args.check_config:
        return _check_config()
    if args.healthcheck:
        return _healthcheck()
    return None


def _check_config() -> int:
    from .config import load_settings  # late: only this flag needs the full Settings

    try:
        settings = load_settings()
    except SystemExit as exc:  # the existing safe message: variable names only
        print(exc.code, file=sys.stderr)
        return 1
    print(f"config ok, mode={settings.payment_mode}")
    return 0


def _healthcheck() -> int:
    healthy, message = check_heartbeat(heartbeat_path(resolve_db_path()))
    if healthy:
        return 0
    print(message)
    return 1
