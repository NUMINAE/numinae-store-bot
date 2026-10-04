"""R5 hardening: safe startup defaults, token-safe failures, and logs that never
carry user content."""

from __future__ import annotations

import logging
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from telegram import Chat, Message, Update, User
from telegram.ext import Application

from bot import db as db_module
from bot import main
from bot.config import Settings, get_settings, load_settings

ROOT = Path(__file__).resolve().parent.parent
# Built from parts, never a real token: 9 digits, a colon, 35 URL-safe characters.
FAKE_TOKEN = "123456789" + ":" + "AAH" + "x" * 32
SENTINEL = "SENTINEL-KEY-123"


@pytest.fixture(autouse=True)
def restore_logging():
    """configure_logging() changes global logger levels; put them back."""
    names = ("", "telegram", "httpx", "httpcore")
    saved = {n: logging.getLogger(n).level for n in names}
    yield
    for name, level in saved.items():
        logging.getLogger(name).setLevel(level)
    get_settings.cache_clear()


@pytest.fixture
def boot_env(tmp_path, monkeypatch):
    db_module.close_conn()
    monkeypatch.setenv("BOT_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("OWNER_ID", "424242")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "boot.db"))
    monkeypatch.setenv("SEED_DEMO", "false")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    get_settings.cache_clear()
    yield
    db_module.close_conn()
    get_settings.cache_clear()


# --- A1: no unsafe defaults -------------------------------------------------------------------


def test_a_missing_payment_mode_is_a_config_error_naming_only_the_variable(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("OWNER_ID", "424242")
    monkeypatch.delenv("PAYMENT_MODE", raising=False)
    with pytest.raises(SystemExit) as info:
        load_settings(env_file=None)
    message = str(info.value)
    assert "PAYMENT_MODE is missing" in message
    assert FAKE_TOKEN not in message and "424242" not in message


def test_main_with_no_payment_mode_exits_cleanly_and_prints_no_configured_value(tmp_path):
    env = {
        "BOT_TOKEN": FAKE_TOKEN,
        "OWNER_ID": "424242",
        "DB_PATH": str(tmp_path / "x.db"),
        "PYTHONPATH": str(ROOT),
        "SYSTEMROOT": "C:\\Windows",  # Windows needs this to start Python at all
    }
    proc = subprocess.run(
        [sys.executable, "-m", "bot.main"],
        cwd=tmp_path,  # no .env here
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    output = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "PAYMENT_MODE is missing" in output
    assert FAKE_TOKEN not in output and "424242" not in output
    assert "Traceback" not in output


def test_seed_demo_defaults_to_false(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("OWNER_ID", "1")
    monkeypatch.setenv("PAYMENT_MODE", "stars")
    monkeypatch.delenv("SEED_DEMO", raising=False)
    assert Settings(_env_file=None).seed_demo is False


def _boot(monkeypatch):
    monkeypatch.setattr(Application, "run_polling", lambda self, *a, **k: None)
    main.main()


def test_mock_mode_logs_exactly_one_demo_warning(boot_env, monkeypatch, caplog):
    monkeypatch.setenv("PAYMENT_MODE", "mock")
    get_settings.cache_clear()
    caplog.set_level(logging.DEBUG)
    _boot(monkeypatch)
    demo = [r for r in caplog.records if "DEMO MODE" in r.getMessage()]
    assert len(demo) == 1 and demo[0].levelno == logging.WARNING
    assert demo[0].getMessage() == (
        "DEMO MODE: Pay (demo) grants real content without payment. "
        "Never run this mode with real content."
    )


def test_stars_mode_logs_exactly_one_info_line_and_no_warning(boot_env, monkeypatch, caplog):
    monkeypatch.setenv("PAYMENT_MODE", "stars")
    get_settings.cache_clear()
    caplog.set_level(logging.DEBUG)
    _boot(monkeypatch)
    mode_lines = [r for r in caplog.records if "Payment mode" in r.getMessage()]
    assert len(mode_lines) == 1
    assert mode_lines[0].levelno == logging.INFO
    assert mode_lines[0].getMessage() == "Payment mode: stars"
    assert not any("DEMO MODE" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("mode", ["mock", "stars"])
def test_the_token_never_appears_in_startup_logs(boot_env, monkeypatch, caplog, mode):
    monkeypatch.setenv("PAYMENT_MODE", mode)
    get_settings.cache_clear()
    caplog.set_level(logging.DEBUG)
    _boot(monkeypatch)
    assert FAKE_TOKEN not in caplog.text and "AAH" + "x" * 32 not in caplog.text


# --- A2: token-safe startup failures ----------------------------------------------------------


def _token_url_error() -> RuntimeError:
    return RuntimeError(f"cannot reach https://api.telegram.org/bot{FAKE_TOKEN}/getMe")


def test_a_polling_crash_is_logged_critical_redacted_and_exits_with_code_1(
    boot_env, monkeypatch, caplog, capsys
):
    monkeypatch.setenv("PAYMENT_MODE", "stars")
    get_settings.cache_clear()

    def explode(self, *args, **kwargs):
        raise _token_url_error()

    monkeypatch.setattr(Application, "run_polling", explode)
    caplog.set_level(logging.INFO)
    with pytest.raises(SystemExit) as info:
        main.main()

    assert info.value.code == 1
    captured = capsys.readouterr()
    assert FAKE_TOKEN not in captured.err + captured.out
    assert FAKE_TOKEN not in caplog.text
    critical = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert len(critical) == 1 and "[redacted]" in critical[0].getMessage()


def test_a_crash_before_logging_is_configured_still_exits_1_without_the_token(
    boot_env, monkeypatch, capsys
):
    monkeypatch.setenv("PAYMENT_MODE", "stars")
    get_settings.cache_clear()
    monkeypatch.setattr(
        main, "configure_logging", lambda: (_ for _ in ()).throw(_token_url_error())
    )
    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", [])  # a bare logger: nothing configured
    with pytest.raises(SystemExit) as info:
        main.main()
    assert info.value.code == 1
    captured = capsys.readouterr()
    assert FAKE_TOKEN not in captured.err + captured.out


def test_the_excepthook_redacts_a_token_in_a_traceback(monkeypatch, capsys):
    try:
        raise _token_url_error()
    except RuntimeError:
        exc_info = sys.exc_info()
    main._redacting_excepthook(*exc_info)
    err = capsys.readouterr().err
    assert FAKE_TOKEN not in err
    assert "Traceback" in err and "[redacted]" in err and "RuntimeError" in err


def test_main_installs_the_excepthook_before_reading_config(monkeypatch):
    seen = {}
    monkeypatch.setattr(sys, "excepthook", sys.__excepthook__)

    def fake_get_settings():
        seen["hook"] = sys.excepthook
        raise SystemExit("stop here")

    monkeypatch.setattr(main, "get_settings", fake_get_settings)
    with pytest.raises(SystemExit):
        main.main()
    assert seen["hook"] is main._redacting_excepthook


def test_a_normal_exit_is_not_turned_into_a_crash(boot_env, monkeypatch):
    monkeypatch.setenv("PAYMENT_MODE", "mock")
    get_settings.cache_clear()
    monkeypatch.setattr(Application, "run_polling", lambda self, *a, **k: None)
    main.main()  # returns normally


# --- A3: no user content in logs --------------------------------------------------------------


def _message_update(text: str) -> Update:
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=5, type="private"),
        from_user=User(id=5, first_name="Pat", is_bot=False),
        text=text,
    )
    return Update(update_id=1, message=message)


def _context(error: BaseException):
    return SimpleNamespace(error=error)


async def test_on_error_never_logs_message_text(caplog):
    caplog.set_level(logging.DEBUG)
    update = _message_update(f"here are my keys {SENTINEL}")
    await main.on_error(update, _context(RuntimeError("handler blew up")))
    assert SENTINEL not in caplog.text
    (record,) = caplog.records
    assert record.levelno == logging.ERROR and record.exc_info  # traceback kept
    text = record.getMessage()
    assert "RuntimeError" in text and "handler blew up" in text
    assert "update=message" in text and "user_id=5" in text


async def test_a_real_handler_that_raises_on_a_text_message_logs_no_content(
    boot_env, monkeypatch, caplog
):
    monkeypatch.setenv("PAYMENT_MODE", "mock")
    get_settings.cache_clear()
    from telegram.ext import MessageHandler, filters

    application = main.build_application()

    async def explode(update, context):
        raise ValueError("boom")  # a real handler failing mid-message

    application.add_handler(MessageHandler(filters.TEXT, explode), group=5)
    caplog.set_level(logging.DEBUG)
    update = _message_update(f"paste {SENTINEL}")
    # PTB routes a handler's exception to on_error, which must log no content.
    # initialize() would call Telegram (getMe); mark it initialized instead.
    application._initialized = True
    await application.process_update(update)
    assert "Unhandled ValueError" in caplog.text  # on_error really ran
    assert SENTINEL not in caplog.text


async def test_on_error_logs_callback_data_truncated_to_64_bytes(caplog):
    from telegram import CallbackQuery

    caplog.set_level(logging.DEBUG)
    query = CallbackQuery(
        id="1",
        from_user=User(id=7, first_name="x", is_bot=False),
        chat_instance="c",
        data="x" * 200,
    )
    await main.on_error(Update(update_id=2, callback_query=query), _context(RuntimeError("e")))
    text = caplog.records[0].getMessage()
    assert "update=callback_query" in text
    assert "x" * 64 in text and "x" * 65 not in text


async def test_on_error_redacts_a_token_in_the_exception_message(caplog):
    caplog.set_level(logging.DEBUG)
    await main.on_error(_message_update("hi"), _context(_token_url_error()))
    assert FAKE_TOKEN not in caplog.records[0].getMessage()


async def test_on_error_handles_non_update_objects(caplog):
    caplog.set_level(logging.DEBUG)
    await main.on_error("garbage", _context(RuntimeError("e")))
    assert "update=str" in caplog.records[0].getMessage()


async def test_ignored_updates_are_not_errors(caplog):
    from bot.tg import IgnoreUpdate

    caplog.set_level(logging.DEBUG)
    await main.on_error(_message_update("x"), _context(IgnoreUpdate("skip")))
    assert not any(r.levelno >= logging.WARNING for r in caplog.records)


@pytest.mark.parametrize("configured", ["DEBUG", "INFO", "WARNING", "ERROR"])
def test_telegram_and_http_loggers_stay_quiet_whatever_log_level_says(
    boot_env, monkeypatch, configured
):
    monkeypatch.setenv("PAYMENT_MODE", "mock")
    monkeypatch.setenv("LOG_LEVEL", configured)
    get_settings.cache_clear()
    main.configure_logging()
    level = getattr(logging, configured)
    assert logging.getLogger().level == level
    assert logging.getLogger("telegram").getEffectiveLevel() >= max(level, logging.INFO)
    assert logging.getLogger("telegram.ext").getEffectiveLevel() >= logging.INFO
    assert logging.getLogger("httpx").getEffectiveLevel() >= max(level, logging.WARNING)
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING


def test_ptb_debug_update_dump_is_suppressed_at_debug(boot_env, monkeypatch, caplog):
    """PTB logs 'Processing update <Update ...>' at DEBUG; with LOG_LEVEL=DEBUG the
    telegram logger must still drop it."""
    monkeypatch.setenv("PAYMENT_MODE", "mock")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    get_settings.cache_clear()
    main.configure_logging()
    caplog.set_level(logging.DEBUG)
    logging.getLogger("telegram.ext.Application").debug("Processing update %s", SENTINEL)
    assert SENTINEL not in caplog.text


# --- A6: parse_cb_id accepts only canonical ASCII ids -------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["+5", " 5", "5 ", "1_0", "٣", "007", "-1", "9223372036854775808", "", "5\n", "0x10", "1e3"]
    + ["５", "²", "00", "-0", "9" * 20],
)
def test_parse_cb_id_rejects_everything_that_is_not_a_canonical_id(value):
    from bot.keyboards import parse_cb_id

    assert parse_cb_id(value) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [("0", 0), ("1", 1), ("123", 123), ("9223372036854775807", 9223372036854775807)],
)
def test_parse_cb_id_accepts_canonical_ids(value, expected):
    from bot.keyboards import parse_cb_id

    assert parse_cb_id(value) == expected


def test_parse_cb_id_never_raises_on_non_strings():
    from bot.keyboards import parse_cb_id

    assert parse_cb_id(None) is None
    assert parse_cb_id(5) is None
