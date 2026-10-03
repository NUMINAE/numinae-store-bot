"""Token redaction: in log messages, log args, formatted tracebacks, the logging
setup in main(), and the refund detail."""

from __future__ import annotations

import io
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot import main, refunds
from bot.redact import REDACTED, RedactingFormatter, redact
from bot.refunds import RefundStatus, refund_order
from bot.repo import orders as orders_repo

from .shop import KEY, key_states, pay, place_order

SUFFIX = "AAEhBOweik6ad9r_QXmV8x4QDQ8QXbmrf0Y"  # exactly 35 token characters
TOKEN = "123456789:" + SUFFIX
URL = f"https://api.telegram.org/bot{TOKEN}/refundStarPayment"


def test_the_fixture_token_has_the_real_shape():
    assert len(SUFFIX) == 35


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (TOKEN, REDACTED),
        (f"token is {TOKEN}.", f"token is {REDACTED}."),
        (URL, f"https://api.telegram.org/bot{REDACTED}/refundStarPayment"),
        (f"{TOKEN} and {TOKEN}", f"{REDACTED} and {REDACTED}"),
        (f"'{TOKEN}'", f"'{REDACTED}'"),
        ("1234567890:" + SUFFIX, REDACTED),  # 10-digit bot id
        ("12345678:" + SUFFIX, REDACTED),  # 8-digit bot id
    ],
)
def test_redact_replaces_token_shaped_strings(text, expected):
    assert redact(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "no secrets here",
        "12:34:56",
        "order 12345678: paid",  # digits and a colon, but not a token body
        "1234567:" + SUFFIX,  # only 7 digits: not a bot id
        "123456789:short",
        "2026-06-15 12:00:00",
        "",
    ],
)
def test_redact_leaves_ordinary_text_alone(text):
    assert redact(text) == text


def _format(record_factory, formatter=None) -> str:
    return (formatter or RedactingFormatter("%(levelname)s %(message)s")).format(record_factory())


def _record(msg, args=(), exc_info=None) -> logging.LogRecord:
    return logging.LogRecord("t", logging.ERROR, __file__, 1, msg, args, exc_info)


def test_a_token_in_a_log_message_is_redacted():
    out = _format(lambda: _record(f"request to {URL} failed"))
    assert TOKEN not in out and "bot[redacted]/refundStarPayment" in out


def test_a_token_in_log_args_is_redacted_after_formatting():
    out = _format(lambda: _record("request to %s failed (%d)", (URL, 404)))
    assert TOKEN not in out and "(404)" in out


def test_a_token_in_a_formatted_traceback_is_redacted():
    try:
        raise RuntimeError(f"HTTP 404 for url {URL}")
    except RuntimeError:
        import sys

        out = _format(lambda: _record("refund failed", exc_info=sys.exc_info()))
    assert TOKEN not in out
    assert "Traceback" in out and "RuntimeError: HTTP 404 for url" in out and REDACTED in out


def test_a_logged_exception_message_is_redacted_end_to_end():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(RedactingFormatter("%(message)s"))
    logger = logging.getLogger("redact-test")
    logger.addHandler(handler)
    logger.propagate = False
    try:
        try:
            raise ConnectionError(f"failed: {URL}")
        except ConnectionError:
            logger.exception("call %s raised", URL)
    finally:
        logger.removeHandler(handler)
    assert TOKEN not in stream.getvalue() and SUFFIX not in stream.getvalue()


def test_main_installs_the_redacting_formatter_on_the_root_handlers(monkeypatch):
    root = logging.getLogger()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    monkeypatch.setattr(root, "handlers", [handler])
    main.configure_logging()

    assert isinstance(handler.formatter, RedactingFormatter)
    logging.getLogger("bot.test").error("leaked %s", URL)
    assert TOKEN not in stream.getvalue() and "[redacted]" in stream.getvalue()


def test_main_adds_a_handler_when_the_root_has_none(monkeypatch):
    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", [])
    main.configure_logging()
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0].formatter, RedactingFormatter)


# --- the refund detail ------------------------------------------------------------------------


def fake_bot() -> SimpleNamespace:
    return SimpleNamespace(refund_star_payment=AsyncMock(return_value=True))


async def test_the_refund_detail_is_clean_when_the_exception_quotes_a_token_url(db, monkeypatch):
    order_id = place_order(3001, {KEY: 2})
    pay(order_id, "ch_tok")
    provider = SimpleNamespace(
        mode="stars",
        refund=AsyncMock(side_effect=RuntimeError(f"Client error '404' for url '{URL}'")),
    )
    monkeypatch.setattr(refunds, "build_provider", lambda mode: provider)
    keys_before = key_states(db)

    result = await refund_order(fake_bot(), order_id, "owner")

    assert result.status is RefundStatus.PROVIDER_FAILED
    assert TOKEN not in result.detail and SUFFIX not in result.detail
    assert REDACTED in result.detail and result.detail.startswith("RuntimeError:")
    assert orders_repo.get(order_id)["status"] == "paid" and key_states(db) == keys_before


async def test_the_token_never_reaches_a_log_record_in_the_refund_flow(db, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    order_id = place_order(3001, {KEY: 2})
    pay(order_id, "ch_tok2")
    provider = SimpleNamespace(
        mode="stars", refund=AsyncMock(side_effect=RuntimeError(f"boom {URL}"))
    )
    monkeypatch.setattr(refunds, "build_provider", lambda mode: provider)
    await refund_order(fake_bot(), order_id, "owner")

    # ... and through the real Stars provider, where Telegram's error quotes the URL.
    from telegram.error import NetworkError

    monkeypatch.undo()
    bot = fake_bot()
    bot.refund_star_payment.side_effect = NetworkError(f"httpx said {URL}")
    again = await refund_order(bot, order_id, "owner")

    assert again.status is RefundStatus.PROVIDER_FAILED
    assert TOKEN not in caplog.text and SUFFIX not in caplog.text
    for record in caplog.records:
        assert TOKEN not in record.getMessage()
        assert record.exc_info is None or TOKEN not in str(record.exc_info[1])
