from __future__ import annotations

import pytest

from bot import render

HOSTILE = "<b>&"
ESCAPED = "&lt;b&gt;&amp;"


def test_esc_escapes_html_special_characters():
    assert render.esc(HOSTILE) == ESCAPED
    assert render.esc("\"quoted\" & 'single'") == "&quot;quoted&quot; &amp; &#x27;single&#x27;"


def test_esc_stringifies_non_strings():
    assert render.esc(42) == "42"
    assert render.esc(None) == "None"


@pytest.mark.parametrize(
    ("func", "open_tag", "close_tag"),
    [
        (render.bold, "<b>", "</b>"),
        (render.code, "<code>", "</code>"),
        (render.strike, "<s>", "</s>"),
        (render.pre, "<pre>", "</pre>"),
    ],
)
def test_formatters_escape_their_content(func, open_tag, close_tag):
    assert func(HOSTILE) == f"{open_tag}{ESCAPED}{close_tag}"


@pytest.mark.parametrize("func", [render.bold, render.code, render.strike, render.pre])
def test_formatters_do_not_double_escape_or_leak_raw_markup(func):
    out = func("<script>alert(1)</script>")
    assert "<script>" not in out
    assert "&amp;lt;" not in out


@pytest.mark.parametrize(
    ("cents", "expected"),
    [
        (0, "$0.00"),
        (5, "$0.05"),
        (100, "$1.00"),
        (1234, "$12.34"),
        (-150, "-$1.50"),
        (-5, "-$0.05"),
    ],
)
def test_usd(cents, expected):
    assert render.usd(cents) == expected
