from __future__ import annotations

import logging
import string

import pytest

from bot import i18n
from bot.repo.orders import ALLOWED_TRANSITIONS

REAL_KEY = "common.my_cart_count"  # {"en": "My cart ({count})", "ru": ...}


def _warnings(caplog):
    return [r for r in caplog.records if r.levelno == logging.WARNING]


def test_translates_into_each_language():
    assert i18n.t(REAL_KEY, "en", count=3) == "My cart (3)"
    assert i18n.t(REAL_KEY, "ru", count=3) == "Моя корзина (3)"


def test_returns_the_template_unchanged_without_kwargs():
    assert i18n.t("common.back_to_menu_button", "en") == "Back to menu"


def test_missing_language_falls_back_to_english_with_one_warning(caplog):
    caplog.set_level(logging.WARNING)
    assert i18n.t(REAL_KEY, "de", count=2) == "My cart (2)"
    (record,) = _warnings(caplog)
    assert "missing language 'de'" in record.getMessage()


def test_entry_lacking_a_language_falls_back_to_english(monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    monkeypatch.setitem(i18n.STRINGS, "test.only_en", {"en": "English only"})
    assert i18n.t("test.only_en", "ru") == "English only"
    assert len(_warnings(caplog)) == 1


def test_missing_key_returns_the_key_with_one_warning_and_no_exception(caplog):
    # There is no English text to fall back to for an unknown key, so the key
    # itself is returned; the screen still renders.
    caplog.set_level(logging.WARNING)
    assert i18n.t("no.such.key", "en") == "no.such.key"
    assert i18n.t("no.such.key2", "ru") == "no.such.key2"
    assert len(_warnings(caplog)) == 2  # one per call


def test_missing_key_and_missing_language_together(caplog):
    caplog.set_level(logging.WARNING)
    assert i18n.t("no.such.key", "de") == "no.such.key"
    assert len(_warnings(caplog)) == 1


def test_key_without_english_or_requested_language_returns_the_key(monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    monkeypatch.setitem(i18n.STRINGS, "test.only_ru", {"ru": "только русский"})
    assert i18n.t("test.only_ru", "de") == "test.only_ru"
    assert len(_warnings(caplog)) == 2


def test_no_kwargs_returns_the_raw_template_silently(caplog):
    caplog.set_level(logging.WARNING)
    assert i18n.t(REAL_KEY, "en") == "My cart ({count})"
    assert _warnings(caplog) == []


def test_format_error_returns_the_raw_template_with_a_warning(caplog):
    caplog.set_level(logging.WARNING)
    assert i18n.t(REAL_KEY, "en", wrong=1) == "My cart ({count})"
    (record,) = _warnings(caplog)
    assert "format error" in record.getMessage()


def test_no_warning_on_the_happy_path(caplog):
    caplog.set_level(logging.WARNING)
    i18n.t(REAL_KEY, "ru", count=1)
    assert _warnings(caplog) == []


# --- table sanity -----------------------------------------------------------


def test_every_string_exists_in_every_supported_language():
    for key, entry in i18n.STRINGS.items():
        assert set(entry) >= set(i18n.LANGUAGES), key


def test_no_translation_needs_a_placeholder_the_english_text_lacks():
    # English is the reference the call sites are written against; a language
    # may legitimately use fewer placeholders (e.g. Russian drops the noun),
    # because str.format ignores unused kwargs, but never more.
    def fields(template: str) -> set[str]:
        return {name for _, name, _, _ in string.Formatter().parse(template) if name}

    for key, entry in i18n.STRINGS.items():
        for lang in i18n.LANGUAGES:
            assert fields(entry[lang]) <= fields(entry["en"]), (key, lang)


@pytest.mark.parametrize("lang", i18n.LANGUAGES)
def test_every_order_status_has_a_label(lang, caplog):
    caplog.set_level(logging.WARNING)
    for status in ALLOWED_TRANSITIONS:
        label = i18n.status_label(status, lang)
        assert label != f"status.{status}"
    assert _warnings(caplog) == []
