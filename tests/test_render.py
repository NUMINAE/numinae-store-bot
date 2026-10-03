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
    ("amount", "expected"),
    [(1, "⭐ 1"), (75, "⭐ 75"), (100000, "⭐ 100000")],
)
def test_stars(amount, expected):
    assert render.stars(amount) == expected


def test_usd_is_gone():
    assert not hasattr(render, "usd")
