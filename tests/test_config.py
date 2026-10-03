"""Settings loading. Every test passes env_file=None (or a temp file) so a
developer's real .env can never leak in, and monkeypatches the environment."""

from __future__ import annotations

from pathlib import Path

import pytest

from bot import config
from bot.config import Settings, get_settings, load_settings

TOKEN = "123456:SECRET-TOKEN-VALUE-xyz"
VARS = ("BOT_TOKEN", "OWNER_ID", "DB_PATH", "LOG_LEVEL", "PAYMENT_MODE", "SEED_DEMO")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in VARS:
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _valid(monkeypatch, **overrides):
    values = {"BOT_TOKEN": TOKEN, "OWNER_ID": "42", **overrides}
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def test_defaults(monkeypatch):
    _valid(monkeypatch)
    s = load_settings(env_file=None)
    assert s.owner_id == 42
    assert s.db_path == Path("data/shop.db")
    assert s.log_level == "INFO"
    assert s.payment_mode == "mock"
    assert s.seed_demo is True


def test_explicit_values_and_log_level_is_case_insensitive(monkeypatch):
    _valid(
        monkeypatch,
        DB_PATH="x/y.db",
        LOG_LEVEL=" debug ",
        PAYMENT_MODE="stars",
        SEED_DEMO="false",
    )
    s = load_settings(env_file=None)
    assert (s.db_path, s.log_level, s.payment_mode, s.seed_demo) == (
        Path("x/y.db"),
        "DEBUG",
        "stars",
        False,
    )


def test_loads_from_a_dotenv_file(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text(f"BOT_TOKEN={TOKEN}\nOWNER_ID=7\n", encoding="utf-8")
    assert load_settings(env_file=str(env)).owner_id == 7


def test_missing_values_exit_naming_each_variable(monkeypatch):
    with pytest.raises(SystemExit) as info:
        load_settings(env_file=None)
    message = str(info.value)
    assert "BOT_TOKEN is missing" in message
    assert "OWNER_ID is missing" in message
    assert "DB_PATH" not in message


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("OWNER_ID", "not-a-number"),
        ("LOG_LEVEL", "chatty"),
        ("PAYMENT_MODE", "bitcoin"),
        ("SEED_DEMO", "maybe"),
        ("BOT_TOKEN", ""),
    ],
)
def test_invalid_values_exit_with_a_clear_message_that_never_echoes_them(
    monkeypatch, capsys, name, value
):
    _valid(monkeypatch)
    monkeypatch.setenv(name, value)
    with pytest.raises(SystemExit) as info:
        load_settings(env_file=None)
    message = str(info.value)
    assert f"{name} is invalid" in message
    for secret in (TOKEN, "not-a-number", "chatty", "bitcoin", "maybe"):
        assert secret not in message


def test_invalid_owner_id_message_never_contains_the_token(monkeypatch):
    _valid(monkeypatch, OWNER_ID="oops")
    with pytest.raises(SystemExit) as info:
        load_settings(env_file=None)
    assert TOKEN not in str(info.value)
    assert "SECRET" not in str(info.value)


def test_token_is_hidden_from_repr_str_and_dumps(monkeypatch):
    _valid(monkeypatch)
    s = load_settings(env_file=None)
    for rendered in (repr(s), str(s), repr(s.bot_token), str(s.bot_token), str(s.model_dump())):
        assert TOKEN not in rendered
    assert s.bot_token.get_secret_value() == TOKEN


def test_get_settings_is_cached_and_clearable(monkeypatch):
    _valid(monkeypatch)
    monkeypatch.setattr(config, "load_settings", lambda: Settings(_env_file=None))
    first = get_settings()
    assert get_settings() is first
    get_settings.cache_clear()
    assert get_settings() is not first


def test_get_secret_value_is_called_in_exactly_one_place():
    root = Path(__file__).resolve().parent.parent / "bot"
    hits = [
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if "get_secret_value" in path.read_text(encoding="utf-8")
    ]
    assert hits == ["main.py"]
