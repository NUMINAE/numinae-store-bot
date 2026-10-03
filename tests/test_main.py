"""Startup wiring: main() against a throwaway environment and database, with
polling stubbed out so nothing talks to Telegram."""

from __future__ import annotations

import pytest
from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    TypeHandler,
)

from bot import db as db_module
from bot import main
from bot.config import get_settings


@pytest.fixture
def boot_env(tmp_path, monkeypatch):
    db_module.close_conn()
    monkeypatch.setenv("DB_PATH", str(tmp_path / "boot.db"))
    monkeypatch.setenv("SEED_DEMO", "true")
    get_settings.cache_clear()
    yield tmp_path / "boot.db"
    db_module.close_conn()
    get_settings.cache_clear()


def _registered(application: Application):
    return [h for group in application.handlers.values() for h in group]


def test_build_application_registers_the_expected_handlers(boot_env):
    application = main.build_application()
    handlers = _registered(application)

    assert -1 in application.handlers
    assert any(isinstance(h, TypeHandler) and h.type is Update for h in application.handlers[-1])

    commands = {c for h in handlers if isinstance(h, CommandHandler) for c in h.commands}
    assert commands == {"start", "admin"}  # /order and /sweep are gone

    patterns = {
        h.pattern.pattern
        for h in handlers
        if isinstance(h, CallbackQueryHandler) and h.pattern is not None
    }
    assert patterns == {r"^cat:", r"^cart:", r"^adm:", r"^lib:", r"^menu:", r"^help:", r"^lang:"}

    text_handlers = [h for h in handlers if isinstance(h, MessageHandler)]
    assert len(text_handlers) == 1  # the photo handler is gone

    catch_all = [h for h in handlers if isinstance(h, CallbackQueryHandler) and h.pattern is None]
    assert len(catch_all) == 1
    assert application.error_handlers


def test_no_background_jobs_are_scheduled(boot_env):
    application = main.build_application()
    assert application.job_queue is not None
    assert application.job_queue.jobs() == ()


def test_main_migrates_seeds_and_starts_polling(boot_env, monkeypatch):
    started = []
    monkeypatch.setattr(Application, "run_polling", lambda self, *a, **k: started.append(self))

    main.main()

    assert len(started) == 1
    assert boot_env.exists()
    conn = db_module.get_conn()
    assert conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] >= 1


def test_main_aborts_with_a_clear_message_when_a_migration_fails(boot_env, monkeypatch):
    def broken() -> list[str]:
        raise db_module.MigrationError("Migration '0002_x.sql' failed and was rolled back: boom")

    monkeypatch.setattr(main, "init_db", broken)
    with pytest.raises(SystemExit) as info:
        main.main()
    assert "0002_x.sql" in str(info.value)


def test_main_does_not_seed_when_disabled(boot_env, monkeypatch):
    monkeypatch.setenv("SEED_DEMO", "false")
    get_settings.cache_clear()
    monkeypatch.setattr(Application, "run_polling", lambda self, *a, **k: None)
    main.main()
    assert db_module.get_conn().execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 0
