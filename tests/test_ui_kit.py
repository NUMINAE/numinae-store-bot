"""The design language's building blocks, and the rules that keep it consistent."""

from __future__ import annotations

import ast
import html
from dataclasses import asdict

import pytest

from bot import ui_kit
from bot.ui_kit import (
    CRUMB_SEP,
    ELLIPSIS,
    HOME_CALLBACK,
    ICON,
    ICON_FIELDS,
    ITEM_LINES_SHOWN,
    NOOP_CALLBACK,
    SEP_META,
    SEP_PRICE,
    btn_label,
    delivery_icon,
    fit_lines,
    nav_row,
    pager,
    screen,
    status_icon,
    trim,
    trim_escaped,
)

from .conftest import OWNER_ID  # noqa: F401 - env must be set before bot.* is imported
from .helpers import HOME
from .screens import markup_problems

HOSTILE = "<b>&"


# --- screen() ---------------------------------------------------------------------------------


def test_a_screen_is_a_header_a_blank_line_and_the_body():
    assert screen(ICON.cart, "Your cart", "body") == f"{ICON.cart} <b>Your cart</b>\n\nbody"


def test_a_screen_without_a_body_is_just_the_header():
    assert screen(ICON.cart, "Your cart") == f"{ICON.cart} <b>Your cart</b>"


def test_crumbs_and_step_sit_between_the_header_and_the_body():
    text = screen(ICON.cart, "T", "body", crumbs=["Catalogue", "Pack"], step=(2, 3, "Payment"))
    assert text.split("\n") == [
        f"{ICON.cart} <b>T</b>",
        f"Catalogue{CRUMB_SEP}Pack",
        f"Step 2 of 3{SEP_META}Payment",
        "",
        "body",
    ]


def test_a_step_without_a_label():
    assert "Step 1 of 3" in screen(ICON.cart, "T", step=(1, 3)).split("\n")


def test_title_crumbs_and_step_label_are_plain_text_and_escaped_exactly_once():
    text = screen(ICON.cart, HOSTILE, crumbs=[HOSTILE, HOSTILE], step=(1, 2, HOSTILE))
    escaped = html.escape(HOSTILE)
    assert text.count(escaped) == 4
    assert "&amp;amp;" not in text and "&amp;lt;" not in text
    assert markup_problems(text) == []  # the hostile "<b>" never became a real tag


def test_the_body_is_already_rendered_html_and_is_not_escaped_again():
    body = "<b>bold</b> &amp; <code>x</code>"
    assert screen(ICON.cart, "T", body).endswith(f"\n\n{body}")


# --- nav_row() --------------------------------------------------------------------------------


def test_nav_row_is_back_then_home_and_home_alone_without_a_back():
    with_back = nav_row(back="cart:view")
    assert [(b.text, b.callback_data) for b in with_back] == [
        (f"{ICON.back} Back", "cart:view"),
        (HOME, HOME_CALLBACK),
    ]
    assert [(b.text, b.callback_data) for b in nav_row()] == [(HOME, HOME_CALLBACK)]


# --- btn_label / trim / trim_escaped / fit_lines ---------------------------------------------


def test_btn_label_collapses_whitespace_and_cuts_with_an_ellipsis():
    assert btn_label("a   b\n c") == "a b c"
    cut = btn_label("x" * 100, 10)
    assert len(cut) == 10 and cut.endswith(ELLIPSIS)
    assert btn_label("   ") == ELLIPSIS  # never empty: Telegram rejects empty labels


@pytest.mark.parametrize("budget", [1, 2, 5, 60])
def test_trim_never_exceeds_its_budget(budget):
    assert len(trim("y" * 200, budget)) <= budget
    assert trim("short", 60) == "short"
    assert trim("y" * 200, budget).endswith(ELLIPSIS)


def test_trim_cuts_raw_text_so_it_can_never_split_an_entity():
    cut = trim("&" * 100, 10)
    assert html.escape(cut).count("&amp;") == 9  # whole entities only
    assert "&am" not in html.escape(cut).replace("&amp;", "")


@pytest.mark.parametrize("budget", [10, 50, 400, 900])
def test_trim_escaped_bounds_the_escaped_length(budget):
    for hostile in ("&" * 2000, "<" * 2000, '"' * 2000, "plain " * 500):
        cut = trim_escaped(hostile, budget)
        assert len(html.escape(cut)) <= budget


def test_trim_escaped_leaves_short_text_alone():
    assert trim_escaped("a & b", 100) == "a & b"


def test_fit_lines_keeps_a_prefix_that_fits_and_reports_the_rest():
    lines = ["a" * 10] * 10
    shown, hidden = fit_lines(lines, 35)  # 10 + 1 + 10 + 1 + 10 = 32
    assert (len(shown), hidden) == (3, 7)
    assert fit_lines(lines, 10_000) == (lines, 0)


def test_fit_lines_always_shows_at_least_one_line():
    shown, hidden = fit_lines(["x" * 500, "y"], 10)
    assert shown == ["x" * 500] and hidden == 1
    assert fit_lines([], 10) == ([], 0)


# --- pager() ----------------------------------------------------------------------------------


def test_no_pager_for_a_single_page():
    assert pager(0, 1, lambda n: f"p:{n}") == []
    assert pager(0, 0, lambda n: f"p:{n}") == []


def test_pager_arrows_are_omitted_at_the_edges():
    def row(page, pages):
        return [(b.text, b.callback_data) for b in pager(page, pages, lambda n: f"p:{n}")]

    assert row(0, 3) == [("1 / 3", NOOP_CALLBACK), (ICON.next, "p:1")]
    assert row(1, 3) == [(ICON.prev, "p:0"), ("2 / 3", NOOP_CALLBACK), (ICON.next, "p:2")]
    assert row(2, 3) == [(ICON.prev, "p:1"), ("3 / 3", NOOP_CALLBACK)]


def test_pager_clamps_an_out_of_range_page():
    row = pager(99, 3, lambda n: f"p:{n}")
    assert row[1].text == "3 / 3"


# --- icons ------------------------------------------------------------------------------------


def test_every_status_and_delivery_kind_has_an_icon():
    for status in ("pending_payment", "paid", "delivered", "cancelled", "expired", "refunded"):
        assert status_icon(status) == getattr(ICON, status)
    for kind in ("file", "key", "text"):
        assert delivery_icon(kind) == getattr(ICON, kind)
    assert status_icon("nonsense") == ICON.info


def test_icon_placeholders_cover_every_icon():
    assert set(ICON_FIELDS) == {f"i_{name}" for name in asdict(ICON)}
    assert all(value for value in ICON_FIELDS.values())


def test_separators_and_limits():
    assert (SEP_META, SEP_PRICE, CRUMB_SEP) == (" · ", " — ", " › ")
    assert ITEM_LINES_SHOWN == 5


# --- icons only come from ICON ----------------------------------------------------------------


def _string_literals(source: str):
    """(line, value) of every string constant that is NOT a docstring or comment."""
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            yield node.lineno, node.value


def test_no_icon_character_is_hard_coded_outside_ui_kit():
    """Emoji for screens come from ICON (strings use "{i_<name>}" placeholders), so a
    restyle is one edit. The icons' own characters must not appear in any other
    string literal under bot/."""
    from pathlib import Path

    glyphs = {value.replace("️", "") for value in asdict(ICON).values()}
    glyphs -= {ICON.plus, ICON.minus, ICON.back}  # ASCII-ish text, not emoji
    offenders = []
    for path in sorted(Path(ui_kit.__file__).parent.rglob("*.py")):
        if path.name == "ui_kit.py":
            continue
        for line, value in _string_literals(path.read_text(encoding="utf-8")):
            stripped = value.replace("️", "")
            if any(glyph in stripped for glyph in glyphs):
                offenders.append(f"{path.name}:{line}")
    assert offenders == []
