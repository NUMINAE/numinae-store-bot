"""Admin flows through the real handlers: dashboard, edition creation with a
delivery kind, typed Stars prices, delivery text, blast-radius counts and the
settings editor. The owner is user id 1 (tests/conftest.py)."""

from __future__ import annotations

import pytest

from bot.handlers import admin_catalogue, catalogue, router
from bot.repo import admin_pending as pending_repo
from bot.repo import customers as customers_repo
from bot.repo import products as products_repo
from bot.repo import settings as settings_repo
from bot.repo import variants as variants_repo

from .conftest import OWNER_ID
from .flow import Flow
from .helpers import HOME, add_customer, make_message, make_update

HOSTILE = "<b>&"


@pytest.fixture
def flow(db, monkeypatch):
    add_customer(OWNER_ID)
    return Flow(monkeypatch, OWNER_ID)


def sent_buttons(flow) -> list[tuple[str, str]]:
    markup = flow.sent_messages()[-1].kwargs["reply_markup"]
    return [(b.text, str(b.callback_data)) for row in markup.inline_keyboard for b in row]


def sent_text(flow) -> str:
    return str(flow.sent_messages()[-1].kwargs["text"])


async def create_edition(flow, label: str = "Studio", price: str = "150", kind: str = "t") -> int:
    """Drive the full three-step edition creation on product 1."""
    await flow.tap(admin_catalogue.route, "adm:va:add:1")
    await flow.send(router.route_text, label)
    await flow.send(router.route_text, price)
    await flow.tap(admin_catalogue.route, f"adm:va:ck:{kind}")
    (row,) = [r for r in variants_repo.list_all_for_product(1) if r["label"] == label]
    return int(row["id"])


# --- dashboard ----------------------------------------------------------------------------------


async def test_dashboard_has_catalogue_orders_and_settings_only(flow):
    update = make_update(OWNER_ID, message=make_message("/admin"), chat_id=OWNER_ID)
    await admin_catalogue.dashboard(update, flow.context)
    labels = [label for label, _ in sent_buttons(flow)]
    assert labels == ["Catalogue", "Orders", "Stats", "Settings", HOME]
    assert "ayment" not in sent_text(flow)


async def test_orders_button_opens_the_orders_screen(flow):
    await flow.tap(admin_catalogue.route, "adm:o")
    assert "Orders" in flow.text
    assert "All (0)" in flow.labels() and flow.data_for("‹ Back") == "adm:home"


async def test_old_payment_and_order_admin_routes_are_gone(flow):
    for data in ("adm:pm", "adm:ord:verify:1"):
        await flow.tap(admin_catalogue.route, data)
        assert flow.alert == "This menu has expired."


# --- edition creation: label -> price -> delivery kind ------------------------------------------


async def test_creating_a_text_edition_walks_label_price_kind(flow):
    await flow.tap(admin_catalogue.route, "adm:va:add:1")
    assert pending_repo.get(OWNER_ID)["action"] == "var_create_label"

    await flow.send(router.route_text, "Studio")
    assert pending_repo.get(OWNER_ID)["action"] == "var_create_price"

    await flow.send(router.route_text, "⭐150")
    assert pending_repo.get(OWNER_ID)["action"] == "var_create_kind"
    assert [label for label, _ in sent_buttons(flow)] == ["File", "Keys", "Text", "Cancel", HOME]
    assert "cannot be changed later" in sent_text(flow)

    await flow.tap(admin_catalogue.route, "adm:va:ck:t")
    assert pending_repo.get(OWNER_ID) is None
    (row,) = [r for r in variants_repo.list_all_for_product(1) if r["label"] == "Studio"]
    assert (row["price_stars"], row["delivery_kind"], row["is_active"]) == (150, "text", 1)
    assert "Studio" in flow.text and "Price: ⭐ 150" in flow.text and "Delivery: Text" in flow.text


async def test_a_label_containing_a_colon_survives_the_pending_value_round_trip(flow):
    variant_id = await create_edition(flow, label="Pro: Team", price="90")
    row = variants_repo.get(variant_id)
    assert (row["label"], row["price_stars"]) == ("Pro: Team", 90)


@pytest.mark.parametrize(("code", "kind"), [("f", "file"), ("k", "key"), ("t", "text")])
async def test_each_delivery_kind_can_be_chosen(flow, code, kind):
    variant_id = await create_edition(flow, kind=code)
    assert variants_repo.get(variant_id)["delivery_kind"] == kind


async def test_choosing_a_kind_without_a_pending_flow_is_expired(flow):
    await flow.tap(admin_catalogue.route, "adm:va:ck:t")
    assert flow.alert == "This menu has expired."
    pending_repo.set(OWNER_ID, "var_create_kind", context_id=1, pending_value="10:X")
    await flow.tap(admin_catalogue.route, "adm:va:ck:z")
    assert flow.alert == "This menu has expired."


async def test_the_kind_is_fixed_after_creation(flow):
    variant_id = await create_edition(flow)
    assert not hasattr(variants_repo, "update_delivery_kind")
    assert not any(
        "kind" in label.lower() or "delivery" in label.lower() and "text" not in label.lower()
        for label, _ in flow.buttons
    )
    assert variants_repo.get(variant_id)["delivery_kind"] == "text"


# --- the "hidden from customers" warning and delivery text --------------------------------------


@pytest.mark.parametrize(
    ("kind", "hint"),
    [("t", "set the delivery text"), ("k", "no keys in stock"), ("f", "upload the file")],
)
async def test_unconfigured_edition_shows_a_hidden_warning(flow, kind, hint):
    await create_edition(flow, kind=kind)
    assert "Hidden from customers: no delivery content yet" in flow.text
    assert hint in flow.text


async def test_key_edition_card_shows_stock_and_no_text_button(flow):
    await create_edition(flow, kind="k")
    assert "Keys: 0 available / 0 reserved / 0 sold / 0 revoked" in flow.text
    assert "Set delivery text" not in flow.labels()


async def test_set_delivery_text_stores_raw_and_clears_the_warning(flow):
    variant_id = await create_edition(flow)
    assert "Set delivery text" in flow.labels()
    assert not products_repo.is_variant_available(products_repo.get_variant(variant_id))

    await flow.tap(admin_catalogue.route, f"adm:va:dt:{variant_id}")
    assert pending_repo.get(OWNER_ID)["action"] == "var_delivery_text"
    await flow.send(router.route_text, HOSTILE)

    assert variants_repo.get(variant_id)["delivery_text"] == HOSTILE  # stored raw
    assert "Hidden from customers" not in flow.text
    assert products_repo.is_variant_available(products_repo.get_variant(variant_id))
    assert pending_repo.get(OWNER_ID) is None


async def test_delivery_text_is_escaped_when_an_admin_card_is_rendered(flow):
    variant_id = await create_edition(flow)
    variants_repo.set_delivery_text(variant_id, HOSTILE)
    await flow.tap(admin_catalogue.route, f"adm:va:v:{variant_id}")
    assert "<b>&" not in flow.text.replace("<b>Studio</b>", "")


@pytest.mark.parametrize("bad", ["   ", "x" * 2001])
async def test_delivery_text_length_is_validated(flow, bad):
    variant_id = await create_edition(flow)
    await flow.tap(admin_catalogue.route, f"adm:va:dt:{variant_id}")
    await flow.send(router.route_text, bad)
    assert "between 1 and 2000 characters" in flow.replies[-1]
    assert pending_repo.get(OWNER_ID) is not None  # still waiting
    assert variants_repo.get(variant_id)["delivery_text"] is None


async def test_delivery_text_accepts_exactly_2000_characters(flow):
    variant_id = await create_edition(flow)
    await flow.tap(admin_catalogue.route, f"adm:va:dt:{variant_id}")
    await flow.send(router.route_text, "x" * 2000)
    assert len(variants_repo.get(variant_id)["delivery_text"]) == 2000


async def test_delivery_text_action_is_text_editions_only(flow):
    variant_id = await create_edition(flow, kind="k")
    await flow.tap(admin_catalogue.route, f"adm:va:dt:{variant_id}")
    assert flow.alert == "This menu has expired."
    assert pending_repo.get(OWNER_ID) is None


# --- Stars prices through the typed-input handler -----------------------------------------------


@pytest.mark.parametrize("bad", ["7.5", "7,5", "0", "⭐0", "-3", "25001", "abc", "", "1e3", "⭐"])
async def test_bad_prices_are_rejected_and_the_edit_stays_pending(flow, bad):
    variant_id = await create_edition(flow, price="10")
    await flow.tap(admin_catalogue.route, f"adm:va:ep:{variant_id}")
    await flow.send(router.route_text, bad)
    assert "price" in flow.replies[-1].lower()
    assert variants_repo.get(variant_id)["price_stars"] == 10
    assert pending_repo.get(OWNER_ID)["action"] == "var_edit_price"


@pytest.mark.parametrize(("good", "expected"), [("75", 75), ("⭐75", 75), ("25000", 25000)])
async def test_good_prices_are_accepted(flow, good, expected):
    variant_id = await create_edition(flow, price="10")
    await flow.tap(admin_catalogue.route, f"adm:va:ep:{variant_id}")
    await flow.send(router.route_text, good)
    assert variants_repo.get(variant_id)["price_stars"] == expected
    assert pending_repo.get(OWNER_ID) is None


async def test_a_bad_price_during_creation_keeps_the_label_step_pending(flow):
    await flow.tap(admin_catalogue.route, "adm:va:add:1")
    await flow.send(router.route_text, "Studio")
    await flow.send(router.route_text, "12.50")
    assert pending_repo.get(OWNER_ID)["action"] == "var_create_price"
    assert not [r for r in variants_repo.list_all_for_product(1) if r["label"] == "Studio"]


# --- customers see an edition only once it is deliverable ---------------------------------------


async def test_a_new_edition_appears_for_customers_only_after_delivery_is_set(flow, monkeypatch):
    variant_id = await create_edition(flow)
    assert variant_id not in [v["id"] for v in products_repo.list_variants(1)]
    variants_repo.set_delivery_text(variant_id, "https://example.com/x")
    assert variant_id in [v["id"] for v in products_repo.list_variants(1)]

    customers_repo.upsert(2002, None, None)
    customer = Flow(monkeypatch, 2002)
    await customer.tap(catalogue.route, "cat:p:1:0")
    assert "Studio — ⭐ 150" in customer.labels()


# --- blast radius counts purchasable editions ---------------------------------------------------


async def test_category_deactivation_counts_only_purchasable_editions(flow):
    # Developer & Design Tools: 2 + 2 + 2 editions, one of them (Team) has no keys.
    await flow.tap(admin_catalogue.route, "adm:cat:tg:2")
    assert "hides 3 products and 5 editions" in flow.text


async def test_product_deactivation_counts_only_purchasable_editions(flow):
    await flow.tap(admin_catalogue.route, "adm:pr:tg:5")  # Pocket Linter: Team has no keys
    assert "hides 1 edition from customers" in flow.text
    await flow.tap(admin_catalogue.route, "adm:pr:tg:1")
    assert "hides 2 editions from customers" in flow.text


async def test_blast_radius_shrinks_when_a_product_is_already_hidden(flow):
    products_repo.set_active(1, False)
    await flow.tap(admin_catalogue.route, "adm:cat:tg:1")
    assert (
        "hides 2 products and 2 editions" in flow.text
    )  # count of all products, purchasable editions


async def test_category_toggle_confirm_hides_and_reactivation_restores(flow):
    await flow.tap(admin_catalogue.route, "adm:cat:tgc:1")
    from bot.repo import categories as categories_repo

    assert 1 not in [c["id"] for c in categories_repo.list_active()]
    await flow.tap(admin_catalogue.route, "adm:cat:tg:1")  # inactive -> straight back on
    assert 1 in [c["id"] for c in categories_repo.list_active()]


async def test_admin_screens_use_edition_wording(flow):
    await flow.tap(admin_catalogue.route, "adm:pr:v:1")
    assert "Editions:" in flow.text
    assert any("Manage editions" in label for label in flow.labels())
    await flow.tap(admin_catalogue.route, "adm:va:1")
    assert "Editions" in flow.text


# --- settings -----------------------------------------------------------------------------------


async def test_settings_screen_lists_the_new_keys_only(flow):
    await flow.tap(admin_catalogue.route, "adm:set")
    for expected in ("Welcome text", "Support note", "Reservation minutes: 15"):
        assert expected in flow.text
    assert "New customer notifications: ON" in flow.text
    for gone in ("Shipping", "Reminder", "expiry"):
        assert gone not in flow.text
        assert not any(gone in label for label in flow.labels())


@pytest.mark.parametrize("bad", ["4", "61", "0", "-5", "abc", "7.5", "", "٧"])
async def test_reservation_minutes_rejects_out_of_range_and_junk(flow, bad):
    await flow.tap(admin_catalogue.route, "adm:set:e:reservation_minutes")
    await flow.send(router.route_text, bad)
    assert "between 5 and 60" in flow.replies[-1]
    assert settings_repo.get("reservation_minutes") == "15"
    assert pending_repo.get(OWNER_ID) is not None


@pytest.mark.parametrize("good", ["5", "30", "60"])
async def test_reservation_minutes_accepts_5_to_60(flow, good):
    await flow.tap(admin_catalogue.route, "adm:set:e:reservation_minutes")
    await flow.send(router.route_text, good)
    assert settings_repo.get("reservation_minutes") == good
    assert pending_repo.get(OWNER_ID) is None


async def test_old_settings_keys_are_not_editable(flow):
    await flow.tap(admin_catalogue.route, "adm:set:e:shipping_note")
    assert flow.alert == "Unknown setting."
    assert pending_repo.get(OWNER_ID) is None


async def test_welcome_text_edit_and_support_note_clear(flow):
    await flow.tap(admin_catalogue.route, "adm:set:e:welcome_text")
    await flow.send(router.route_text, "Hello creators")
    assert settings_repo.get("welcome_text") == "Hello creators"

    await flow.tap(admin_catalogue.route, "adm:set:e:support_note")
    await flow.send(router.route_text, "-")
    assert settings_repo.get("support_note") == ""


async def test_notify_new_customers_toggle(flow):
    await flow.tap(admin_catalogue.route, "adm:set:tgnc")
    assert settings_repo.get("notify_new_customers") == "0"
    assert "New customer notifications: OFF" in flow.text
    await flow.tap(admin_catalogue.route, "adm:set:tgnc")
    assert settings_repo.get("notify_new_customers") == "1"
