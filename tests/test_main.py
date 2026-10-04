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
    PreCheckoutQueryHandler,
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
    assert commands == {"start", "admin", "paysupport", "sweep", "order", "library"}

    patterns = {
        h.pattern.pattern
        for h in handlers
        if isinstance(h, CallbackQueryHandler) and h.pattern is not None
    }
    assert patterns == {
        r"^cat:",
        r"^cart:",
        r"^co:",
        r"^adm:",
        r"^lib:",
        r"^menu:",
        r"^help:",
    }

    message_handlers = [h for h in handlers if isinstance(h, MessageHandler)]
    # free text, successful_payment, owner uploads (documents) and the other-media hint
    assert {h.callback.__name__ for h in message_handlers} == {
        "route_text",
        "successful_payment",
        "on_document",
        "on_other_media",
    }
    assert len([h for h in handlers if isinstance(h, PreCheckoutQueryHandler)]) == 1

    catch_all = [h for h in handlers if isinstance(h, CallbackQueryHandler) and h.pattern is None]
    assert len(catch_all) == 1
    assert application.error_handlers


def test_the_recurring_sweep_is_scheduled(boot_env):
    application = main.build_application()
    assert application.job_queue is not None
    (job,) = application.job_queue.jobs()
    assert job.name == "sweep_job"
    assert job.trigger.interval.total_seconds() == 60


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


# --- R2: payments wiring ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "expected"), [("mock", "MockProvider"), ("stars", "StarsProvider")]
)
def test_the_provider_is_built_from_the_payment_mode_into_bot_data(
    boot_env, monkeypatch, mode, expected
):
    monkeypatch.setenv("PAYMENT_MODE", mode)
    get_settings.cache_clear()
    application = main.build_application()
    provider = application.bot_data["payment_provider"]
    assert type(provider).__name__ == expected and provider.mode == mode


def test_main_boots_in_mock_mode_and_registers_every_handler(boot_env, monkeypatch):
    monkeypatch.setenv("PAYMENT_MODE", "mock")
    get_settings.cache_clear()
    captured = []
    monkeypatch.setattr(Application, "run_polling", lambda self, *a, **k: captured.append(self))

    main.main()

    (application,) = captured
    assert application.bot_data["payment_provider"].mode == "mock"
    handlers = [h for group in application.handlers.values() for h in group]
    callbacks = {h.callback.__name__ for h in handlers}
    for name in (
        "start",
        "dashboard",
        "paysupport",
        "sweep_command",
        "route",
        "route_text",
        "pre_checkout",
        "successful_payment",
        "menu_show",
        "help_placeholder",
        "unknown_callback",
        "throttle",
        "find_order",
        "on_document",
        "on_other_media",
    ):
        assert name in callbacks, name
    patterns = {
        h.pattern.pattern
        for h in handlers
        if isinstance(h, CallbackQueryHandler) and h.pattern is not None
    }
    assert {r"^co:", r"^lib:", r"^cat:", r"^cart:", r"^adm:"} <= patterns
    assert [j.name for j in application.job_queue.jobs()] == ["sweep_job"]
    assert db_module.get_conn().execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 3


def test_uploads_and_order_handlers_are_registered_before_the_catch_all(boot_env):
    application = main.build_application()
    group0 = application.handlers[0]
    names = [getattr(h, "callback", None) and h.callback.__name__ for h in group0]
    catch_all = next(
        i for i, h in enumerate(group0) if isinstance(h, CallbackQueryHandler) and h.pattern is None
    )
    for name in (
        "find_order",
        "on_document",
        "on_other_media",
        "pre_checkout",
        "successful_payment",
    ):
        assert names.index(name) < catch_all, name
