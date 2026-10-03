"""Callback-route security properties: every admin route refuses a non-owner
without side effects, and malformed callback data never raises in any
namespace."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from bot.handlers import admin_catalogue, cart, catalogue, checkout, common, library
from bot.keyboards import CALLBACK_DATA_MAX_BYTES

from .conftest import OWNER_ID
from .flow import Flow
from .helpers import add_customer

STRANGER = 2002

# One concrete callback for every route the "adm:" router (and the settings
# sub-router) understands. test_route_table_covers_every_action_in_source keeps
# this list honest as routes are added.
ADM_ROUTES = [
    "adm:home",
    "adm:o",
    "adm:cancel",
    "adm:set",
    "adm:set:v:welcome_text",
    "adm:set:e:welcome_text",
    "adm:set:tgnc",
    "adm:cat",
    "adm:cat:add",
    "adm:cat:v:1",
    "adm:cat:en:1",
    "adm:cat:ed:1",
    "adm:cat:so:1",
    "adm:cat:tg:1",
    "adm:cat:tgc:1",
    "adm:pr:1:0",
    "adm:pr:add:1",
    "adm:pr:v:1",
    "adm:pr:en:1",
    "adm:pr:ed:1",
    "adm:pr:so:1",
    "adm:pr:mv:1",
    "adm:pr:mvto:1:2",
    "adm:pr:tg:1",
    "adm:pr:tgc:1",
    "adm:va:1",
    "adm:va:add:1",
    "adm:va:v:1",
    "adm:va:el:1",
    "adm:va:ep:1",
    "adm:va:so:1",
    "adm:va:tg:1",
    "adm:va:tgc:1",
    "adm:va:dt:1",
    "adm:va:ck:t",
    "adm:va:uf:1",
    "adm:va:ak:1",
    "adm:o:l:all:0",
    "adm:o:l:attention:0",
    "adm:o:v:1",
    "adm:ord:cancel:1",
    "adm:ord:cancel:1:yes",
    "adm:ord:retry:1",
    "adm:ord:resend:1",
    "adm:ord:refund:1",
    "adm:ord:refund:1:yes",
    "adm:st",
]


def _snapshot(conn) -> list[str]:
    return list(conn.iterdump())


@pytest.mark.parametrize("data", ADM_ROUTES)
async def test_non_owner_is_refused_on_every_admin_route(db, monkeypatch, data):
    add_customer(STRANGER)
    flow = Flow(monkeypatch, STRANGER)
    before = _snapshot(db)

    await flow.tap(admin_catalogue.route, data)

    assert flow.alert == "You are not authorized to do that."
    flow.edit.assert_not_called()  # nothing rendered
    flow.context.bot.send_message.assert_not_called()
    assert _snapshot(db) == before  # nothing written (incl. pending input)


async def test_route_table_covers_every_action_in_source():
    root = Path(__file__).resolve().parent.parent / "bot" / "handlers"
    source = (root / "admin_catalogue.py").read_text(encoding="utf-8")
    for name in ("admin_settings.py", "admin_orders.py", "admin_stats.py", "admin_uploads.py"):
        source += (root / name).read_text(encoding="utf-8")
    # Route actions are bare words; pending-input actions ("set_value", ...) contain "_".
    actions = set(re.findall(r'action == "([a-z]+)"', source))
    actions |= set(re.findall(r'resource == "([a-z]+)"', source))
    covered = {part for route in ADM_ROUTES for part in route.split(":")[1:]}
    # "v"/"e" etc. are per-resource actions; every literal the router compares
    # against must appear somewhere in the table above.
    assert actions - covered == set()


# --- malformed callbacks --------------------------------------------------------------------

NAMESPACES: dict[str, list[str]] = {
    "cat": ["cats", "cat", "p", "v", "q", "add"],
    "cart": ["view", "inc", "dec", "remove", "clear", "checkout"],
    "co": ["start", "pay", "resume", "cancel", "cancelyes", "cancelno", "mockpay"],
    "lib": ["list", "item", "send"],
    "lang": ["show", "set"],
    "adm": sorted({route.split(":")[1] for route in ADM_ROUTES}),
}
HANDLERS = {
    "cat": catalogue.route,
    "cart": cart.route,
    "co": checkout.route,
    "lib": library.route,
    "lang": common.lang_route,
    "adm": admin_catalogue.route,
}


def _malformed(namespace: str, action: str) -> list[str]:
    return [
        namespace,  # too few parts
        f"{namespace}:{action}:",  # empty trailing segment
        f"{namespace}::1",  # empty action
        f"{namespace}:{action}::",  # empty ids
        f"{namespace}:{action}:abc:abc:abc",  # non-numeric ids
        f"{namespace}:{action}:-1:-1:-1",  # negative ids
        f"{namespace}:{action}:99999999999999999999",  # absurdly large id
        f"{namespace}:{action}:1:2:3:4:5:6:7:8:9",  # too many parts
        f"{namespace}:{action}:1:" + "x" * 40,  # junk tail
    ]


CASES = [
    (namespace, data)
    for namespace, actions in NAMESPACES.items()
    for action in actions
    for data in _malformed(namespace, action)
]


@pytest.mark.parametrize(("namespace", "data"), CASES)
async def test_malformed_callback_data_never_raises_and_is_answered(
    db, monkeypatch, namespace, data
):
    add_customer(OWNER_ID)  # the owner, so adm: routes pass owner_only
    flow = Flow(monkeypatch, OWNER_ID)

    await flow.tap(HANDLERS[namespace], data)  # must not raise

    flow.query.answer.assert_awaited()  # the tap was acknowledged either way


def test_every_route_fits_telegrams_callback_limit():
    for route in ADM_ROUTES:
        assert len(route.encode()) <= CALLBACK_DATA_MAX_BYTES


# --- ownership of orders and library items (co: and lib:) -----------------------------------------

OWNER_USER = 3001
STRANGER_USER = 2002
GENERIC = "That order is no longer available."


def _order_routes(order_id: int) -> list[str]:
    return [
        f"co:resume:{order_id}",
        f"co:cancel:{order_id}",
        f"co:cancelyes:{order_id}",
        f"co:cancelno:{order_id}",
        f"co:mockpay:{order_id}",
    ]


@pytest.mark.parametrize("route_index", range(5))
async def test_a_strangers_order_callbacks_get_the_same_response_as_a_missing_order(
    db, monkeypatch, route_index
):
    from bot.repo import orders as orders_repo

    from .shop import place_order

    order_id = place_order(OWNER_USER, mode="mock")
    add_customer(STRANGER_USER)
    before = _snapshot(db)

    stranger = Flow(monkeypatch, STRANGER_USER)
    await stranger.tap(checkout.route, _order_routes(order_id)[route_index])
    on_real_order = stranger.alert
    probe = Flow(monkeypatch, STRANGER_USER)
    await probe.tap(checkout.route, _order_routes(999999)[route_index])

    assert on_real_order == probe.alert == GENERIC
    stranger.edit.assert_not_called()
    assert _snapshot(db) == before  # nothing changed
    assert orders_repo.get(order_id)["status"] == "pending_payment"


@pytest.mark.parametrize("route", ["lib:item:{id}", "lib:send:{id}"])
async def test_a_strangers_library_item_gets_the_same_response_as_a_missing_item(
    db, monkeypatch, route
):
    from bot.repo import orders as orders_repo

    from .shop import pay, place_order

    order_id = place_order(OWNER_USER, mode="mock")
    pay(order_id)
    (item,) = orders_repo.get_with_items(order_id)[1]
    add_customer(STRANGER_USER)

    stranger = Flow(monkeypatch, STRANGER_USER)
    await stranger.tap(library.route, route.format(id=item["id"]))
    probe = Flow(monkeypatch, STRANGER_USER)
    await probe.tap(library.route, route.format(id=999999))

    assert stranger.alert == probe.alert == GENERIC
    stranger.edit.assert_not_called()
    stranger.context.bot.send_message.assert_not_called()
