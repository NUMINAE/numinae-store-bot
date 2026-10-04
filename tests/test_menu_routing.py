"""Reply-keyboard routing: persistent-keyboard taps arrive as plain text, so
only an EXACT label match may dispatch."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from bot.handlers import router
from bot.keyboards import MENU_LABELS

from .conftest import OWNER_ID
from .helpers import make_context, make_message, make_update

LABELS = list(MENU_LABELS)
ACTION_BY_INDEX = ["show_categories", "show_cart", "show_library"]


@pytest.fixture
def targets(monkeypatch):
    from bot.handlers import cart, catalogue, library

    mocks = {
        "show_categories": AsyncMock(),
        "show_cart": AsyncMock(),
        "show_library": AsyncMock(),
    }
    monkeypatch.setattr(catalogue, "show_categories", mocks["show_categories"])
    monkeypatch.setattr(cart, "show_cart", mocks["show_cart"])
    monkeypatch.setattr(library, "show_library", mocks["show_library"])
    return mocks


def _assert_only(targets, expected: str | None):
    for name, mock in targets.items():
        if name == expected:
            mock.assert_awaited_once()
        else:
            mock.assert_not_called()


@pytest.mark.parametrize("label", LABELS)
async def test_exact_labels_dispatch(targets, label):
    index = MENU_LABELS.index(label)
    update = make_update(1001, message=make_message(label))
    assert await router._dispatch_menu_button(update, make_context(), label) is True
    _assert_only(targets, ACTION_BY_INDEX[index])


NEAR_MISSES = [
    "{label} ",  # trailing space
    " {label}",
    "{label}.",
    "{label}\n12 Some Street\nTown",  # an address that merely contains a label
    "please open {label}",
    "{lower}",
    "{upper}",
    "{label}{label}",
]


@pytest.mark.parametrize("label", LABELS)
@pytest.mark.parametrize("template", NEAR_MISSES)
async def test_lines_that_merely_contain_a_label_do_not_dispatch(targets, label, template):
    text = template.format(label=label, lower=label.lower(), upper=label.upper())
    update = make_update(1001, message=make_message(text))
    assert await router._dispatch_menu_button(update, make_context(), text) is False
    _assert_only(targets, None)


async def test_unrelated_text_does_not_dispatch(targets):
    update = make_update(1001, message=make_message("hello"))
    assert await router._dispatch_menu_button(update, make_context(), "hello") is False
    _assert_only(targets, None)


async def test_route_text_dispatches_an_exact_label_before_anything_else(db, targets):
    label = MENU_LABELS[1]
    update = make_update(1001, message=make_message(label))
    await router.route_text(update, make_context())
    _assert_only(targets, "show_cart")


async def test_route_text_does_not_dispatch_a_longer_line_containing_a_label(
    db, targets, monkeypatch
):
    # Owner with a pending admin input: the typed value must reach the admin
    # handler even if it happens to contain a menu label.
    from bot.repo import admin_pending as admin_pending_repo

    admin_pending_repo.set(OWNER_ID, "cat_name")
    sentinel = AsyncMock()
    monkeypatch.setattr(router.admin_catalogue, "handle_pending_input", sentinel)

    text = f"Best {MENU_LABELS[0]} in town"
    update = make_update(OWNER_ID, message=make_message(text))
    await router.route_text(update, make_context())

    sentinel.assert_awaited_once()
    _assert_only(targets, None)


def _from_code_points(codes: str) -> str:
    """No Cyrillic literal in the source: build the old labels from hex code points."""
    return "".join(chr(int(code, 16)) for code in codes.split())


FORMER_RUSSIAN_LABELS = (
    _from_code_points(
        "041f 0440 043e 0441 043c 043e 0442 0440 0020 0442 043e 0432 0430 0440 043e 0432"
    ),
    _from_code_points("041a 043e 0440 0437 0438 043d 0430"),
    _from_code_points("041c 043e 044f 0020 0431 0438 0431 043b 0438 043e 0442 0435 043a 0430"),
)


@pytest.mark.parametrize("label", FORMER_RUSSIAN_LABELS)
async def test_the_former_russian_labels_no_longer_dispatch(targets, label):
    update = make_update(1001, message=make_message(label))
    assert await router._dispatch_menu_button(update, make_context(), label) is False
    _assert_only(targets, None)
