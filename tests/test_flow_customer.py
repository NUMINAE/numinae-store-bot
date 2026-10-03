"""Customer flows through the real handlers against the demo catalogue.

Demo ids (see bot/seed_demo.json): categories 1-3; products 1-7; editions
1/2 = Neon Portrait Prompt Pack (text, 75/250), 7/8 = Orbit Icons Pro (keys,
10 each), 9 = Pocket Linter Personal (keys), 10 = Pocket Linter Team (keys,
none in stock, hidden)."""

from __future__ import annotations

import re

import pytest

from bot.handlers import cart, catalogue, common
from bot.repo import cart as cart_repo
from bot.repo import categories as categories_repo
from bot.repo import products as products_repo
from bot.repo import variants as variants_repo

from .flow import Flow
from .helpers import add_customer

USER = 1001
TEXT_EDITION = 1
KEY_EDITION = 7  # 10 keys
EMPTY_KEY_EDITION = 10  # 0 keys
HOSTILE = "<b>&"


@pytest.fixture
def flow(db, monkeypatch):
    add_customer(USER)
    return Flow(monkeypatch, USER)


def _sell_all_keys(db, variant_id: int) -> None:
    db.execute("UPDATE license_keys SET status = 'sold' WHERE variant_id = ?", (variant_id,))
    db.commit()


# --- browse -> product -> edition -> add to cart -------------------------------


async def test_browsing_walks_category_product_edition_and_adds_to_cart(flow):
    await flow.tap(catalogue.route, "cat:cats")
    assert flow.labels()[:3] == [
        "AI Creator Assets",
        "Developer & Design Tools",
        "Guides & E-books",
    ]

    await flow.tap(catalogue.route, flow.data_for("AI Creator Assets"))
    assert "Neon Portrait Prompt Pack — from ⭐ 75" in flow.labels()

    await flow.tap(catalogue.route, flow.data_for("Neon Portrait"))
    assert "Personal — ⭐ 75" in flow.labels()
    assert "Commercial — ⭐ 250" in flow.labels()

    await flow.tap(catalogue.route, flow.data_for("Personal"))
    assert "Neon Portrait Prompt Pack" in flow.text
    assert "Edition: Personal" in flow.text
    assert "Price: ⭐ 75" in flow.text
    assert "Delivery: Delivered as a message" in flow.text

    await flow.tap(catalogue.route, flow.data_for("Add to cart"))
    assert "Added 1 × " in flow.text
    assert cart_repo.summary(USER) == (1, 75)


@pytest.mark.parametrize(
    ("variant", "delivery"),
    [(TEXT_EDITION, "Delivered as a message"), (KEY_EDITION, "Licence key")],
)
async def test_edition_screen_shows_the_delivery_line(flow, variant, delivery):
    await flow.tap(catalogue.route, f"cat:v:{variant}:0")
    assert f"Delivery: {delivery}" in flow.text


async def test_file_edition_screen_says_instant_download(flow, db):
    db.execute(
        "UPDATE product_variants SET delivery_kind = 'file', delivery_file_id = 'f' WHERE id = 2"
    )
    db.commit()
    await flow.tap(catalogue.route, "cat:v:2:0")
    assert "Delivery: Instant download" in flow.text
    assert not any(d.startswith("cat:q:") for _, d in flow.buttons)  # no stepper


async def test_text_edition_has_no_stepper_and_adding_twice_says_so(flow):
    await flow.tap(catalogue.route, f"cat:v:{TEXT_EDITION}:0")
    assert not any(d.startswith("cat:q:") for _, d in flow.buttons)
    add = flow.data_for("Add to cart")

    await flow.tap(catalogue.route, add)
    assert cart_repo.current_qty(USER, TEXT_EDITION) == 1

    await flow.tap(catalogue.route, add)
    assert flow.alert == "This edition is already in your cart."
    assert cart_repo.current_qty(USER, TEXT_EDITION) == 1


# --- key editions: stepper clamped to stock ------------------------------------


async def test_key_edition_stepper_is_clamped_with_a_boundary_alert(flow):
    await flow.tap(catalogue.route, f"cat:v:{KEY_EDITION}:0")
    assert "Total for 1: ⭐ 50" in flow.text

    await flow.tap(catalogue.route, flow.data_for("−"))
    assert flow.alert == "Minimum quantity is 1."

    for expected in range(2, 11):
        await flow.tap(catalogue.route, flow.data_for("+"))
        assert f"Total for {expected}: ⭐ {50 * expected}" in flow.text

    await flow.tap(catalogue.route, flow.data_for("+"))
    assert flow.alert == "Only 10 available — that's the maximum."
    assert "Total for 10" in flow.text  # screen unchanged


async def test_stepper_quantity_travels_in_callback_data_and_is_added(flow):
    await flow.tap(catalogue.route, f"cat:v:{KEY_EDITION}:0")
    await flow.tap(catalogue.route, flow.data_for("+"))
    await flow.tap(catalogue.route, flow.data_for("+"))
    add = flow.data_for("Add to cart")
    assert add == f"cat:add:{KEY_EDITION}:0:3"
    await flow.tap(catalogue.route, add)
    assert cart_repo.current_qty(USER, KEY_EDITION) == 3
    assert "Added 3 × " in flow.text


async def test_adding_more_than_stock_clamps_and_a_full_line_alerts(flow):
    await flow.tap(catalogue.route, f"cat:add:{KEY_EDITION}:0:99")
    assert cart_repo.current_qty(USER, KEY_EDITION) == 10
    assert "That's all the stock there is." in flow.text

    await flow.tap(catalogue.route, f"cat:add:{KEY_EDITION}:0:1")
    assert flow.alert == "Only 10 available — that's the maximum."
    assert cart_repo.current_qty(USER, KEY_EDITION) == 10


async def test_stock_running_out_after_the_screen_was_shown_alerts_out_of_stock(flow, db):
    await flow.tap(catalogue.route, f"cat:v:{KEY_EDITION}:0")
    add = flow.data_for("Add to cart")
    _sell_all_keys(db, KEY_EDITION)
    await flow.tap(catalogue.route, add)
    assert flow.alert == "Sorry, this edition is out of stock."
    assert cart_repo.current_qty(USER, KEY_EDITION) == 0


async def test_the_zero_key_edition_is_hidden_and_cannot_be_opened_or_added(flow):
    await flow.tap(catalogue.route, "cat:p:5:0")
    assert "Team" not in " ".join(flow.labels())
    assert any("Personal" in label for label in flow.labels())

    await flow.tap(catalogue.route, f"cat:v:{EMPTY_KEY_EDITION}:0")
    assert flow.alert == "Sorry, this edition is out of stock."
    assert "Pocket Linter Licence" in flow.text  # sent back to the product card

    await flow.tap(catalogue.route, f"cat:add:{EMPTY_KEY_EDITION}:0:1")
    assert flow.alert == "Sorry, this edition is out of stock."
    assert cart_repo.current_qty(USER, EMPTY_KEY_EDITION) == 0


async def test_a_deactivated_edition_says_no_longer_available(flow):
    variants_repo.set_active(TEXT_EDITION, False)
    await flow.tap(catalogue.route, f"cat:add:{TEXT_EDITION}:0:1")
    assert flow.alert == "This item is no longer available."


# --- visibility cascade ----------------------------------------------------------


async def test_deactivating_a_category_hides_it_and_reactivating_restores_it(flow):
    categories_repo.set_active(1, False)
    await flow.tap(catalogue.route, "cat:cats")
    assert "AI Creator Assets" not in flow.labels()
    await flow.tap(catalogue.route, "cat:cat:1:0")  # stale button
    assert "AI Creator Assets" not in flow.labels()

    categories_repo.set_active(1, True)
    await flow.tap(catalogue.route, "cat:cats")
    assert "AI Creator Assets" in flow.labels()
    await flow.tap(catalogue.route, "cat:cat:1:0")
    assert "Neon Portrait Prompt Pack — from ⭐ 75" in flow.labels()


async def test_empty_catalogue_says_nothing_is_available(db, monkeypatch):
    for c in categories_repo.list_all():
        categories_repo.set_active(c["id"], False)
    add_customer(USER)
    flow = Flow(monkeypatch, USER)
    await flow.tap(catalogue.route, "cat:cats")
    assert "Nothing is available right now" in flow.text


# --- admin-edited text is escaped for customers ----------------------------------------


ESCAPED = "&lt;b&gt;&amp;"


def assert_escaped(text: str) -> None:
    """The hostile string appears only in escaped form: every ampersand starts
    an entity and the only tags are the ones the bot itself writes."""
    assert ESCAPED in text
    assert "&" not in re.sub(r"&(?:amp|lt|gt);", "", text)
    assert set(re.findall(r"<[^>]*>", text)) <= {"<b>", "</b>", "<s>", "</s>"}


@pytest.mark.parametrize(
    ("entity", "screen"),
    [
        ("category", "cat:cat:1:0"),
        ("product", "cat:p:1:0"),
        ("edition", f"cat:v:{TEXT_EDITION}:0"),
    ],
)
async def test_each_hostile_name_renders_literally_on_its_own_screen(flow, entity, screen):
    """Acceptance: "<b>&" set as only the category / product / edition name
    shows up escaped, and nowhere raw, on the customer screen for that level."""
    if entity == "category":
        categories_repo.update_name(1, HOSTILE)
    elif entity == "product":
        products_repo.update_name(1, HOSTILE)
    else:
        variants_repo.update_label(TEXT_EDITION, HOSTILE)

    await flow.tap(catalogue.route, screen)
    assert_escaped(flow.text)


async def test_hostile_admin_text_renders_literally_to_customers(flow):
    products_repo.update_name(1, HOSTILE)
    products_repo.update_description(1, HOSTILE)
    variants_repo.update_label(TEXT_EDITION, HOSTILE)
    categories_repo.update_name(1, HOSTILE)

    await flow.tap(catalogue.route, "cat:cat:1:0")
    assert_escaped(flow.text)  # category name in the heading

    await flow.tap(catalogue.route, "cat:p:1:0")
    assert_escaped(flow.text)

    await flow.tap(catalogue.route, f"cat:v:{TEXT_EDITION}:0")
    assert_escaped(flow.text)

    await flow.tap(catalogue.route, f"cat:add:{TEXT_EDITION}:0:1")
    assert_escaped(flow.text)

    await flow.tap(cart.route, "cart:view")
    assert_escaped(flow.text)


# --- cart ---------------------------------------------------------------------------------------


async def test_cart_lines_show_product_edition_quantity_and_stars(flow):
    cart_repo.add(USER, TEXT_EDITION, 1)
    cart_repo.add(USER, KEY_EDITION, 3)
    await flow.tap(cart.route, "cart:view")
    assert "Neon Portrait Prompt Pack — Personal × 1 — ⭐ 75" in flow.text
    assert "Orbit Icons Pro — Personal × 3 — ⭐ 150" in flow.text
    assert "Subtotal: ⭐ 225" in flow.text


async def test_cart_controls_step_key_lines_and_remove(flow):
    cart_repo.add(USER, KEY_EDITION, 2)
    await flow.tap(cart.route, "cart:view")
    await flow.tap(cart.route, flow.data_for("+"))
    assert "× 3 — ⭐ 150" in flow.text
    await flow.tap(cart.route, flow.data_for("−"))
    await flow.tap(cart.route, flow.data_for("−"))
    assert "× 1 — ⭐ 50" in flow.text
    await flow.tap(cart.route, flow.data_for("−"))  # to zero removes the line
    assert flow.text == "Your cart is empty."

    cart_repo.add(USER, TEXT_EDITION, 1)
    await flow.tap(cart.route, "cart:view")
    await flow.tap(cart.route, flow.data_for("✕"))
    assert flow.text == "Your cart is empty."


async def test_the_plus_button_is_hidden_at_the_cap_and_a_stale_one_alerts(flow):
    cart_repo.add(USER, KEY_EDITION, 9)
    await flow.tap(cart.route, "cart:view")
    assert "+" in flow.labels()
    await flow.tap(cart.route, flow.data_for("+"))  # 9 -> 10
    assert "+" not in flow.labels()

    await flow.tap(cart.route, f"cart:inc:{KEY_EDITION}:10")  # stale message
    assert flow.alert == "Only 10 available — that's the maximum."
    assert cart_repo.current_qty(USER, KEY_EDITION) == 10


async def test_text_and_file_lines_have_no_stepper_buttons(flow):
    cart_repo.add(USER, TEXT_EDITION, 1)
    await flow.tap(cart.route, "cart:view")
    assert "+" not in flow.labels() and "−" not in flow.labels()
    assert "✕" in flow.labels()


async def test_a_line_removed_because_stock_hit_zero_is_announced(flow, db):
    cart_repo.add(USER, KEY_EDITION, 3)
    await flow.tap(cart.route, "cart:view")
    plus = flow.data_for("+")
    _sell_all_keys(db, KEY_EDITION)
    await flow.tap(cart.route, plus)
    assert "Orbit Icons Pro — Personal was removed from your cart" in flow.text
    assert "out of stock" in flow.text
    assert cart_repo.current_qty(USER, KEY_EDITION) == 0


async def test_decrementing_a_line_whose_stock_vanished_is_also_announced(flow, db):
    cart_repo.add(USER, KEY_EDITION, 3)
    await flow.tap(cart.route, "cart:view")
    minus = flow.data_for("−")
    _sell_all_keys(db, KEY_EDITION)
    await flow.tap(cart.route, minus)
    assert "removed from your cart because it is now out of stock" in flow.text


async def test_removing_a_line_yourself_adds_no_stock_notice(flow):
    cart_repo.add(USER, KEY_EDITION, 1)
    cart_repo.add(USER, TEXT_EDITION, 1)
    await flow.tap(cart.route, "cart:view")
    await flow.tap(cart.route, flow.data_for("−"))
    assert "out of stock" not in flow.text


async def test_unavailable_lines_are_annotated_excluded_and_only_removable(flow):
    cart_repo.add(USER, TEXT_EDITION, 1)
    cart_repo.add(USER, KEY_EDITION, 2)
    variants_repo.set_active(KEY_EDITION, False)
    await flow.tap(cart.route, "cart:view")
    assert "(no longer available)" in flow.text
    assert "Subtotal: ⭐ 75" in flow.text
    remove = [label for label in flow.labels() if label.startswith("✕ Remove")]
    assert len(remove) == 1 and "Orbit Icons Pro" in remove[0]
    unavailable_data = [d for _, d in flow.buttons if d.endswith(f":{KEY_EDITION}")]
    assert unavailable_data == [f"cart:remove:{KEY_EDITION}"]


async def test_clear_cart_asks_first_and_no_keeps_the_cart(flow):
    cart_repo.add(USER, TEXT_EDITION, 1)
    cart_repo.add(USER, KEY_EDITION, 2)
    await flow.tap(cart.route, "cart:view")
    await flow.tap(cart.route, flow.data_for("Clear cart"))
    assert flow.text == "Clear all 3 items from your cart?"
    assert cart_repo.summary(USER) == (3, 175)  # nothing cleared yet

    await flow.tap(cart.route, flow.data_for("No"))
    assert cart_repo.summary(USER) == (3, 175)

    await flow.tap(cart.route, "cart:clear:ask")
    await flow.tap(cart.route, flow.data_for("Yes, clear"))
    assert flow.text == "Your cart is empty."
    assert cart_repo.list_lines(USER) == []


async def test_checkout_is_a_stub_with_a_way_back(flow):
    cart_repo.add(USER, TEXT_EDITION, 1)
    await flow.tap(cart.route, "cart:view")
    await flow.tap(cart.route, flow.data_for("Checkout"))
    assert flow.text == "Checkout opens in the next release."
    assert "Back to cart" in flow.labels()
    await flow.tap(cart.route, flow.data_for("Back to cart"))
    assert "Your cart" in flow.text


async def test_malformed_cart_callbacks_alert_instead_of_crashing(flow):
    for data in ("cart:inc:x:1", "cart:remove", "cart:bogus", "cart:clear"):
        await flow.tap(cart.route, data)
        assert flow.alert == "This menu has expired."


# --- main menu, library stub, language ----------------------------------------------------------


async def test_main_menu_has_my_library_and_no_orders_button(flow):
    text, keyboard = common.render_main_menu(USER, "en")
    labels = [b.text for row in keyboard.inline_keyboard for b in row]
    datas = [b.callback_data for row in keyboard.inline_keyboard for b in row]
    assert "My Library" in labels and "lib:show" in datas
    assert not any("order" in label.lower() for label in labels)
    assert not any(str(d).startswith("ord:") for d in datas)


async def test_library_button_shows_the_stub(flow):
    await flow.tap(common.library_route, "lib:show")
    assert "library will appear here" in flow.text


async def test_language_switch_resends_the_reply_keyboard_in_the_new_language(flow):
    await flow.tap(common.lang_route, "lang:set:ru")
    (call,) = flow.sent_messages()
    keyboard = call.kwargs["reply_markup"]
    assert [b.text for b in keyboard.keyboard[0]] == [
        "Просмотр товаров",
        "Корзина",
        "Моя библиотека",
    ]

    await flow.tap(common.lang_route, "lang:set:en")
    keyboard = flow.sent_messages()[-1].kwargs["reply_markup"]
    assert [b.text for b in keyboard.keyboard[0]] == [
        "Browse Products",
        "Shopping Cart",
        "My Library",
    ]


async def test_unknown_old_order_buttons_fall_through_to_menu_expired(flow):
    await flow.tap(common.unknown_callback, "ord:list:0")
    assert flow.alert == "This menu has expired."
