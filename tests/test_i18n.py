"""bot/i18n.py: the single English string table, t() and tn()."""

from __future__ import annotations

import ast
import inspect
import logging
import re
import string
from pathlib import Path

import pytest

from bot import i18n
from bot.payments.checks import REJECTION_MESSAGE_KEY
from bot.repo.orders import ALLOWED_TRANSITIONS

REAL_KEY = "common.my_cart_count"  # "My cart ({count})"
BOT_DIR = Path(__file__).resolve().parent.parent / "bot"
CYRILLIC = re.compile(f"[{chr(0x400)}-{chr(0x4FF)}]")  # no Cyrillic literal in the source


def _warnings(caplog):
    return [r for r in caplog.records if r.levelno == logging.WARNING]


def _fields(template: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


# --- t() ----------------------------------------------------------------------------------------


def test_t_formats_the_template():
    assert i18n.t(REAL_KEY, count=3) == "My cart (3)"


def test_t_returns_the_template_unchanged_without_kwargs_and_silently(caplog):
    caplog.set_level(logging.WARNING)
    assert i18n.t("common.browse_button") == "Browse products"
    assert i18n.t(REAL_KEY) == "My cart ({count})"
    assert _warnings(caplog) == []


def test_a_missing_key_returns_the_key_with_exactly_one_warning(caplog):
    caplog.set_level(logging.WARNING)
    assert i18n.t("no.such.key") == "no.such.key"
    (record,) = _warnings(caplog)
    assert "missing key" in record.getMessage() and "no.such.key" in record.getMessage()


def test_a_missing_key_warns_once_per_call_and_never_raises(caplog):
    caplog.set_level(logging.WARNING)
    assert i18n.t("no.such.key", count=1) == "no.such.key"
    assert i18n.t("no.such.key2") == "no.such.key2"
    assert len(_warnings(caplog)) == 2


def test_a_format_error_returns_the_raw_template_with_a_warning(caplog):
    caplog.set_level(logging.WARNING)
    assert i18n.t(REAL_KEY, wrong=1) == "My cart ({count})"
    (record,) = _warnings(caplog)
    assert "format error" in record.getMessage()


def test_no_warning_on_the_happy_path(caplog):
    caplog.set_level(logging.WARNING)
    i18n.t(REAL_KEY, count=1)
    assert _warnings(caplog) == []


def test_t_no_longer_takes_a_language():
    assert list(inspect.signature(i18n.t).parameters) == ["key", "kwargs"]
    assert not hasattr(i18n, "LANGUAGES") and not hasattr(i18n, "DEFAULT_LANGUAGE")
    assert all(isinstance(value, str) for value in i18n.STRINGS.values())


def test_there_is_no_cyrillic_anywhere_in_the_table():
    assert not CYRILLIC.search(" ".join(i18n.STRINGS.values()))


# --- tn() ----------------------------------------------------------------------------------------

PLURAL_BASES = sorted(
    {key.rsplit(".", 1)[0] for key in i18n.STRINGS if key.endswith((".one", ".other"))}
)


def test_every_plural_base_has_both_forms_and_a_count_placeholder():
    assert PLURAL_BASES, "expected plural strings"
    for base in PLURAL_BASES:
        assert f"{base}.one" in i18n.STRINGS and f"{base}.other" in i18n.STRINGS, base
        for form in ("one", "other"):
            assert "count" in _fields(i18n.STRINGS[f"{base}.{form}"]), (base, form)


PLURAL_CASES = [
    (
        "cart.clear_confirm",
        ("Clear {n} item from your cart?", "Clear all {n} items from your cart?"),
    ),
    (
        "checkout.dropped_note",
        (
            "{n} unavailable item was left out of this order.",
            "{n} unavailable items were left out of this order.",
        ),
    ),
    ("library.count", ("{n} item", "{n} items")),
    ("admin.keys_added", ("Added {n} key.", "Added {n} keys.")),
    ("admin.duplicates_skipped", ("Skipped {n} duplicate.", "Skipped {n} duplicates.")),
    ("admin.categories", ("{n} category", "{n} categories")),
    ("admin.products", ("{n} product", "{n} products")),
    ("admin.editions", ("{n} edition", "{n} editions")),
    (
        "admin.paid_awaiting",
        ("{n} paid order awaiting delivery", "{n} paid orders awaiting delivery"),
    ),
    ("admin.keys_revoked", ("{n} key revoked", "{n} keys revoked")),
    ("admin.items_delivered", ("Delivered {n} item.", "Delivered {n} items.")),
    (
        "owner.more_customers",
        ("{n} more new customer in the last hour", "{n} more new customers in the last hour"),
    ),
]

_SINGULAR_NOUNS = r"(?:item|key|product|edition|category|order|duplicate|customer)"
_PLURAL_NOUNS = r"(?:items|keys|products|editions|categories|orders|duplicates|customers)"


@pytest.mark.parametrize("n", [0, 1, 2, 11])
@pytest.mark.parametrize(("base", "forms"), PLURAL_CASES, ids=[c[0] for c in PLURAL_CASES])
def test_plurals_are_correct_for_0_1_2_and_11(base, forms, n):
    one, other = forms
    text = i18n.tn(base, n)
    assert text == (one if n == 1 else other).format(n=n)
    if n == 1:
        assert not re.search(rf"\b1 (?:\w+ )?{_PLURAL_NOUNS}\b", text), text  # no "1 items"
    else:
        assert not re.search(rf"\b{n} (?:\w+ )?{_SINGULAR_NOUNS}\b", text), text  # no "2 item"


@pytest.mark.parametrize("n", [0, 1, 2, 11])
def test_tn_passes_extra_kwargs_alongside_the_count(n):
    lines = i18n.tn("admin.invalid_lines", n, lines="2, 5")
    assert lines.startswith(f"Skipped {n} invalid line") and lines.endswith("2, 5.")
    assert ("lines" in lines) == (n != 1)
    noun = "item" if n == 1 else "items"
    assert i18n.tn("admin.resent", n, sent=1) == f"Re-sent 1 of {n} {noun} to the buyer."


def test_a_missing_plural_base_returns_the_form_key_with_a_warning(caplog):
    caplog.set_level(logging.WARNING)
    assert i18n.tn("no.such", 1) == "no.such.one"
    assert i18n.tn("no.such", 2) == "no.such.other"
    assert len(_warnings(caplog)) == 2


# --- table sanity and call sites ---------------------------------------------------------------


@pytest.mark.parametrize("status", sorted(ALLOWED_TRANSITIONS))
def test_every_order_status_has_a_label(status, caplog):
    caplog.set_level(logging.WARNING)
    assert i18n.status_label(status) != f"status.{status}"
    assert _warnings(caplog) == []


def _i18n_calls():
    """(file, func, key, keyword names, has **kwargs) for each t()/tn() call
    whose first argument is a string literal."""
    for path in sorted(BOT_DIR.rglob("*.py")):
        if path.name == "i18n.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            if node.func.id not in ("t", "tn") or not node.args:
                continue
            first = node.args[0]
            if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                continue
            kwargs = {kw.arg for kw in node.keywords if kw.arg is not None}
            splat = any(kw.arg is None for kw in node.keywords)
            yield path.name, node.func.id, first.value, kwargs, splat


def test_the_scan_actually_finds_call_sites():
    assert len(list(_i18n_calls())) > 100


def test_every_literal_key_passed_to_t_or_tn_exists():
    missing = []
    for filename, func, key, _, _ in _i18n_calls():
        keys = [key] if func == "t" else [f"{key}.one", f"{key}.other"]
        missing += [(filename, k) for k in keys if k not in i18n.STRINGS]
    assert missing == []


def test_every_call_site_supplies_all_of_its_strings_placeholders():
    problems = []
    for filename, func, key, kwargs, splat in _i18n_calls():
        if splat:
            continue
        keys = [key] if func == "t" else [f"{key}.one", f"{key}.other"]
        supplied = kwargs | ({"count"} if func == "tn" else set())
        for k in keys:
            needed = {
                f for f in _fields(i18n.STRINGS[k]) if not f.startswith("i_")
            }  # icons are automatic
            if not needed <= supplied:
                problems.append((filename, k, sorted(needed - supplied)))
    assert problems == []


_KEY_SHAPE = re.compile(
    r"^(?:common|catalogue|cart|checkout|pay|deliver|delivery|library|jobs|owner|status|admin)"
    r"\.[a-z_.]+$"
)


def test_every_string_literal_in_bot_shaped_like_a_key_exists():
    """Catches keys that reach t() through a variable: any literal shaped like a
    table key must be in the table (or be a plural base)."""
    unknown = []
    for path in sorted(BOT_DIR.rglob("*.py")):
        if path.name == "i18n.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                value = node.value
                if _KEY_SHAPE.match(value) and not (
                    value in i18n.STRINGS or f"{value}.one" in i18n.STRINGS
                ):
                    unknown.append((path.name, value))
    assert unknown == []


def test_dynamic_key_families_are_complete():
    for kind in ("file", "key", "text"):
        assert f"delivery.{kind}" in i18n.STRINGS
    for key in REJECTION_MESSAGE_KEY.values():
        assert key in i18n.STRINGS


def test_no_string_in_the_table_is_empty():
    assert all(value.strip() for value in i18n.STRINGS.values())
