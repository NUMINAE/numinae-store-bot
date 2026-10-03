"""Category and product edit/create prompts: happy path, invalid input, the
pending input surviving invalid input, and Cancel clearing it."""

from __future__ import annotations

import pytest

from bot.handlers import admin_catalogue, router
from bot.repo import admin_pending as pending_repo
from bot.repo import categories as categories_repo
from bot.repo import products as products_repo

from .conftest import OWNER_ID
from .flow import Flow
from .helpers import add_customer


@pytest.fixture
def flow(db, monkeypatch):
    add_customer(OWNER_ID)
    return Flow(monkeypatch, OWNER_ID)


# kind -> (route prefix, repo, id, name/desc/sort pending actions)
KINDS = {
    "category": ("adm:cat", categories_repo, "cat"),
    "product": ("adm:pr", products_repo, "prod"),
}
FIELDS = {
    # field -> (callback action, valid input, expected column value, invalid inputs)
    "name": ("en", "Renamed", "Renamed", ["", "   ", "x" * 61]),
    "description": ("ed", "A fresh description", "A fresh description", ["x" * 501]),
    "sort_order": ("so", "42", 42, ["abc", "1000", "-1", "4.5", ""]),
}
PENDING_ACTION = {"name": "edit_name", "description": "edit_desc", "sort_order": "edit_sort"}

CASES = [(kind, field) for kind in KINDS for field in FIELDS]


def _row(kind: str, row_id: int = 1):
    return KINDS[kind][1].get(row_id)


@pytest.mark.parametrize(("kind", "field"), CASES)
async def test_edit_prompt_sets_a_pending_input_for_the_target(flow, kind, field):
    prefix, _, short = KINDS[kind]
    action, *_ = FIELDS[field]
    await flow.tap(admin_catalogue.route, f"{prefix}:{action}:1")
    pending = pending_repo.get(OWNER_ID)
    assert pending["action"] == f"{short}_{PENDING_ACTION[field]}"
    assert pending["target_id"] == 1
    assert "Cancel" in [label for label, _ in flow.buttons]


@pytest.mark.parametrize(("kind", "field"), CASES)
async def test_valid_input_is_applied_and_clears_the_pending_input(flow, kind, field):
    prefix, _, _ = KINDS[kind]
    action, good, expected, _ = FIELDS[field]
    await flow.tap(admin_catalogue.route, f"{prefix}:{action}:1")
    await flow.send(router.route_text, good)

    column = "sort_order" if field == "sort_order" else field
    assert _row(kind)[column] == expected
    assert pending_repo.get(OWNER_ID) is None
    assert flow.replies[-1]  # the owner got a confirmation


@pytest.mark.parametrize(("kind", "field"), CASES)
async def test_invalid_input_changes_nothing_and_the_pending_input_survives(flow, kind, field):
    prefix, _, _ = KINDS[kind]
    action, _, _, bad_inputs = FIELDS[field]
    column = "sort_order" if field == "sort_order" else field
    original = _row(kind)[column]
    await flow.tap(admin_catalogue.route, f"{prefix}:{action}:1")
    pending_before = dict(pending_repo.get(OWNER_ID))

    for bad in bad_inputs:
        await flow.send(router.route_text, bad)
        assert _row(kind)[column] == original
        assert dict(pending_repo.get(OWNER_ID)) == pending_before  # still waiting
        assert flow.replies[-1]  # told what was wrong

    # ... and a later valid answer still goes through.
    await flow.send(router.route_text, FIELDS[field][1])
    assert _row(kind)[column] == FIELDS[field][2]
    assert pending_repo.get(OWNER_ID) is None


@pytest.mark.parametrize(("kind", "field"), CASES)
async def test_cancel_clears_the_pending_input_and_returns_to_the_detail_screen(flow, kind, field):
    prefix, _, _ = KINDS[kind]
    action, _, _, _ = FIELDS[field]
    column = "sort_order" if field == "sort_order" else field
    original = _row(kind)[column]
    await flow.tap(admin_catalogue.route, f"{prefix}:{action}:1")
    assert pending_repo.get(OWNER_ID) is not None

    await flow.tap(admin_catalogue.route, "adm:cancel")

    assert pending_repo.get(OWNER_ID) is None
    assert _row(kind)[column] == original
    assert _row(kind)["name"] in flow.text.replace("&amp;", "&")


async def test_cleared_description_dash_empties_it(flow):
    categories_repo.update_description(1, "something")
    await flow.tap(admin_catalogue.route, "adm:cat:ed:1")
    await flow.send(router.route_text, "-")
    assert categories_repo.get(1)["description"] == ""


async def test_cancel_with_nothing_pending_shows_the_dashboard(flow):
    await flow.tap(admin_catalogue.route, "adm:cancel")
    assert "Admin dashboard" in flow.text


# --- creation prompts -------------------------------------------------------------------------


async def test_creating_a_category_and_a_product(flow):
    await flow.tap(admin_catalogue.route, "adm:cat:add")
    assert pending_repo.get(OWNER_ID)["action"] == "cat_create_name"
    await flow.send(router.route_text, "x" * 61)  # invalid: stays pending
    assert pending_repo.get(OWNER_ID)["action"] == "cat_create_name"
    await flow.send(router.route_text, "Templates")
    (created,) = [c for c in categories_repo.list_all() if c["name"] == "Templates"]

    await flow.tap(admin_catalogue.route, f"adm:pr:add:{created['id']}")
    assert pending_repo.get(OWNER_ID)["action"] == "prod_create_name"
    await flow.send(router.route_text, "")  # invalid
    assert pending_repo.get(OWNER_ID) is not None
    await flow.send(router.route_text, "Starter")
    assert [p["name"] for p in products_repo.list_all_in_category(created["id"], 10, 0)] == [
        "Starter"
    ]
    assert pending_repo.get(OWNER_ID) is None


@pytest.mark.parametrize("action", ["adm:cat:add", "adm:pr:add:1"])
async def test_cancelling_a_creation_prompt_creates_nothing(flow, action):
    before = (categories_repo.count_all(), products_repo.count_all())
    await flow.tap(admin_catalogue.route, action)
    await flow.tap(admin_catalogue.route, "adm:cancel")
    assert pending_repo.get(OWNER_ID) is None
    assert (categories_repo.count_all(), products_repo.count_all()) == before


# --- the rest of the category / product management screens ---------------------------------------


@pytest.mark.parametrize(
    "data",
    ["adm:home", "adm:cat", "adm:cat:v:1", "adm:pr:1:0", "adm:pr:v:1", "adm:pr:mv:1"]
    + ["adm:va:1", "adm:va:v:1", "adm:set"],
)
async def test_management_screens_render_for_the_owner(flow, data):
    await flow.tap(admin_catalogue.route, data)
    assert flow.text


async def test_unknown_ids_fall_back_to_the_list_instead_of_failing(flow):
    for data in ("adm:cat:v:999", "adm:pr:v:999", "adm:pr:1000:0", "adm:va:v:999", "adm:va:999"):
        await flow.tap(admin_catalogue.route, data)
        assert flow.text  # some screen was rendered


async def test_moving_a_product_changes_its_category(flow):
    await flow.tap(admin_catalogue.route, "adm:pr:mv:1")
    await flow.tap(admin_catalogue.route, "adm:pr:mvto:1:2")
    assert products_repo.get(1)["category_id"] == 2


async def test_product_toggle_confirm_and_reactivate(flow):
    await flow.tap(admin_catalogue.route, "adm:pr:tg:1")
    assert "Deactivate" in flow.text
    await flow.tap(admin_catalogue.route, "adm:pr:tgc:1")
    assert products_repo.get(1)["is_active"] == 0
    await flow.tap(admin_catalogue.route, "adm:pr:tg:1")
    assert products_repo.get(1)["is_active"] == 1


async def test_edition_toggle_warns_when_it_is_the_only_active_one(flow):
    from bot.repo import variants as variants_repo

    await flow.tap(admin_catalogue.route, "adm:va:tg:1")
    assert 'Deactivate "Personal"' in flow.text
    variants_repo.set_active(2, False)
    await flow.tap(admin_catalogue.route, "adm:va:tg:1")
    assert "only edition" in flow.text
    await flow.tap(admin_catalogue.route, "adm:va:tgc:1")
    assert variants_repo.get(1)["is_active"] == 0
    await flow.tap(admin_catalogue.route, "adm:va:tg:1")  # inactive -> reactivates
    assert variants_repo.get(1)["is_active"] == 1
