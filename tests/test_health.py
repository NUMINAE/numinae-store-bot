"""The heartbeat file and the --check-config / --healthcheck flags."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.ext import Application

from bot import health, jobs, main
from bot.config import get_settings

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "123456789" + ":" + "AAH" + "x" * 32
OWNER = "424242"


@pytest.fixture(autouse=True)
def never_poll(monkeypatch):
    def forbidden(self, *args, **kwargs):
        raise AssertionError("run_polling must not be called by a health flag")

    monkeypatch.setattr(Application, "run_polling", forbidden)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # a developer's own .env must never leak into these tests
    monkeypatch.setenv("BOT_TOKEN", TOKEN)
    monkeypatch.setenv("OWNER_ID", OWNER)
    monkeypatch.setenv("PAYMENT_MODE", "mock")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "data" / "shop.db"))
    get_settings.cache_clear()
    yield tmp_path / "data"
    get_settings.cache_clear()


def _age(path: Path, seconds: float, *, now: float) -> None:
    os.utime(path, (now - seconds, now - seconds))


# --- the heartbeat file -------------------------------------------------------------------------


def test_a_fresh_heartbeat_is_healthy(tmp_path):
    beat = tmp_path / "heartbeat"
    beat.touch()
    _age(beat, 10, now=1_000_000)
    assert health.check_heartbeat(beat, now=lambda: 1_000_000) == (True, "ok, heartbeat 10s old")


def test_a_heartbeat_at_the_limit_is_fresh_and_one_second_over_is_stale(tmp_path):
    beat = tmp_path / "heartbeat"
    beat.touch()
    _age(beat, 180, now=2_000_000)
    assert health.check_heartbeat(beat, now=lambda: 2_000_000)[0] is True
    _age(beat, 181, now=2_000_000)
    assert health.check_heartbeat(beat, now=lambda: 2_000_000) == (False, "stale heartbeat 181s")


def test_a_missing_heartbeat_is_unhealthy(tmp_path):
    assert health.check_heartbeat(tmp_path / "heartbeat") == (False, "no heartbeat")


def test_the_heartbeat_lives_beside_the_database():
    assert health.heartbeat_path(Path("/app/data/shop.db")) == Path("/app/data/heartbeat")


def test_touch_creates_and_refreshes_the_file(tmp_path):
    beat = tmp_path / "sub" / "heartbeat"
    health.touch_heartbeat(beat)
    assert beat.exists()
    old = beat.stat().st_mtime - 1000
    os.utime(beat, (old, old))
    health.touch_heartbeat(beat)
    assert beat.stat().st_mtime > old + 500


def test_touch_never_raises(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    health.touch_heartbeat(blocker / "inside" / "heartbeat")  # the "directory" is a file


# --- the sweep touches it -----------------------------------------------------------------------


async def test_the_sweep_touches_the_heartbeat_even_when_the_pass_raises(db, monkeypatch):
    beat = health.heartbeat_path(get_settings().db_path)
    assert not beat.exists()
    monkeypatch.setattr(jobs, "run_sweep", AsyncMock(side_effect=RuntimeError("pass failed")))
    await jobs.sweep_job(SimpleNamespace(bot=SimpleNamespace()))  # never raises
    assert beat.exists()


async def test_the_heartbeat_is_touched_at_the_start_of_the_pass(db, monkeypatch):
    beat = health.heartbeat_path(get_settings().db_path)
    seen = {}

    async def passing(bot, **kwargs):
        seen["existed_at_start"] = beat.exists()
        return jobs.SweepReport(0, 0, 0, 0)

    monkeypatch.setattr(jobs, "run_sweep", passing)
    await jobs.sweep_job(SimpleNamespace(bot=SimpleNamespace()))
    assert seen["existed_at_start"] is True


async def test_a_failing_heartbeat_does_not_stop_the_sweep(db, monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(health, "touch_heartbeat", boom)
    run = AsyncMock(return_value=jobs.SweepReport(0, 0, 0, 0))
    monkeypatch.setattr(jobs, "run_sweep", run)
    await jobs.sweep_job(SimpleNamespace(bot=SimpleNamespace()))
    run.assert_awaited_once()


# --- --check-config -----------------------------------------------------------------------------


def test_check_config_prints_exactly_one_line_and_exits_0(env, capsys):
    with pytest.raises(SystemExit) as info:
        main.main(["--check-config"])
    assert info.value.code == 0
    captured = capsys.readouterr()
    assert captured.out == "config ok, mode=mock\n" and captured.err == ""
    assert TOKEN not in captured.out + captured.err and OWNER not in captured.out + captured.err
    assert not env.exists()  # touches no database, not even the data directory


def test_check_config_reports_the_chosen_mode(env, monkeypatch, capsys):
    monkeypatch.setenv("PAYMENT_MODE", "stars")
    get_settings.cache_clear()
    with pytest.raises(SystemExit) as info:
        main.main(["--check-config"])
    assert info.value.code == 0 and capsys.readouterr().out == "config ok, mode=stars\n"


def test_check_config_with_a_bad_config_prints_the_safe_error_and_exits_1(env, monkeypatch, capsys):
    monkeypatch.delenv("PAYMENT_MODE")
    monkeypatch.setenv("OWNER_ID", "not-a-number")
    with pytest.raises(SystemExit) as info:
        main.main(["--check-config"])
    assert info.value.code == 1
    captured = capsys.readouterr()
    text = captured.out + captured.err
    assert "PAYMENT_MODE is missing" in text and "OWNER_ID is invalid" in text
    assert TOKEN not in text and "not-a-number" not in text
    assert not env.exists()


# --- --healthcheck ------------------------------------------------------------------------------


def test_healthcheck_needs_no_token_and_passes_on_a_fresh_heartbeat(env, monkeypatch, capsys):
    for name in ("BOT_TOKEN", "OWNER_ID", "PAYMENT_MODE"):
        monkeypatch.delenv(name)
    health.touch_heartbeat(env / "heartbeat")
    with pytest.raises(SystemExit) as info:
        main.main(["--healthcheck"])
    assert info.value.code == 0
    assert capsys.readouterr().out == ""


def test_healthcheck_fails_with_one_line_when_missing(env, capsys):
    with pytest.raises(SystemExit) as info:
        main.main(["--healthcheck"])
    assert info.value.code == 1
    assert capsys.readouterr().out == "no heartbeat\n"


def test_healthcheck_fails_with_one_line_when_stale(env, capsys):
    health.touch_heartbeat(env / "heartbeat")
    beat = env / "heartbeat"
    stamp = beat.stat().st_mtime - 600
    os.utime(beat, (stamp, stamp))
    with pytest.raises(SystemExit) as info:
        main.main(["--healthcheck"])
    assert info.value.code == 1
    line = capsys.readouterr().out
    assert line.startswith("stale heartbeat ") and line.endswith("s\n") and line.count("\n") == 1


def test_healthcheck_prints_no_secret(env, capsys):
    with pytest.raises(SystemExit):
        main.main(["--healthcheck"])
    captured = capsys.readouterr()
    assert TOKEN not in captured.out + captured.err and OWNER not in captured.out + captured.err


def test_unknown_flags_are_a_usage_error_not_a_start(env):
    with pytest.raises(SystemExit) as info:
        main.main(["--nonsense"])
    assert info.value.code == 2


def test_the_two_flags_cannot_be_combined(env):
    with pytest.raises(SystemExit) as info:
        main.main(["--check-config", "--healthcheck"])
    assert info.value.code == 2


# --- end to end, as the container runs them ------------------------------------------------------


def _run(flag, tmp_path, **extra_env):
    env = {"PYTHONPATH": str(ROOT), "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""), **extra_env}
    return subprocess.run(
        [sys.executable, "-m", "bot.main", flag],
        cwd=tmp_path,  # no .env here
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_the_check_config_flag_as_a_real_process(tmp_path):
    proc = _run(
        "--check-config",
        tmp_path,
        BOT_TOKEN=TOKEN,
        OWNER_ID=OWNER,
        PAYMENT_MODE="mock",
        DB_PATH=str(tmp_path / "x" / "shop.db"),
    )
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, "config ok, mode=mock\n", "")
    assert not (tmp_path / "x").exists()


def test_the_healthcheck_flag_as_a_real_process_without_any_token(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    env = {"DB_PATH": str(data / "shop.db")}
    missing = _run("--healthcheck", tmp_path, **env)
    assert (missing.returncode, missing.stdout) == (1, "no heartbeat\n")
    health.touch_heartbeat(data / "heartbeat")
    assert _run("--healthcheck", tmp_path, **env).returncode == 0
