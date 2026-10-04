"""Everything delivery sends is escaped exactly once: key values, text bodies, split
parts, file captions and the receipt's item names, for hostile content that tries to
close a <code> tag or open a <script>."""

from __future__ import annotations

import html

import pytest

from bot import delivery
from bot.repo import orders as orders_repo
from bot.repo import products as products_repo
from bot.repo import variants as variants_repo

from .screens import markup_problems
from .shop import KEY, TEXT
from .test_delivery import fake_bot, paid_order

HOSTILE = ["<b>&", "</code><script>alert(1)</script>"]


def _texts(bot) -> list[str]:
    return [str(c.kwargs["text"]) for c in bot.send_message.await_args_list]


def _clean(text: str) -> None:
    assert markup_problems(text) == [], text  # known tags only, balanced, no &amp;amp;
    assert "<script" not in text and "</script" not in text
    assert "<b>&" not in text  # a raw "<b>&" would be a real tag followed by a bare "&"


@pytest.mark.parametrize("hostile", HOSTILE)
async def test_hostile_key_values_are_escaped_inside_code_tags(db, hostile):
    order_id = paid_order({KEY: 2})
    (item,) = orders_repo.get_with_items(order_id)[1]
    db.execute("DELETE FROM license_keys WHERE order_item_id = ?", (item["id"],))
    keys = [f"A-{hostile}-1", f"B-{hostile}-2"]
    db.executemany(
        "INSERT INTO license_keys (variant_id, key_value, status, order_item_id)"
        " VALUES (?, ?, 'sold', ?)",
        [(KEY, k, item["id"]) for k in keys],
    )
    db.commit()
    bot = fake_bot()
    await delivery.deliver_order(bot, order_id)

    message = _texts(bot)[0]
    _clean(message)
    assert message.count("<code>") == 2 and message.count("</code>") == 2
    for key in keys:
        assert f"<code>{html.escape(key)}</code>" in message


@pytest.mark.parametrize("hostile", HOSTILE)
async def test_hostile_text_delivery_is_escaped(db, hostile):
    variants_repo.set_delivery_text(TEXT, f"see {hostile} here")
    bot = fake_bot()
    await delivery.deliver_order(bot, paid_order({TEXT: 1}))
    message = _texts(bot)[0]
    _clean(message)
    assert html.escape(f"see {hostile} here") in message


@pytest.mark.parametrize("hostile", HOSTILE)
async def test_every_part_of_a_split_delivery_is_escaped_and_nothing_is_lost(db, hostile):
    body = ((hostile + "&" * 30 + " ") * 40)[:2000]  # escapes to ~6000 characters
    variants_repo.set_delivery_text(TEXT, body)
    bot = fake_bot()
    await delivery.deliver_order(bot, paid_order({TEXT: 1}))

    parts = [m for m in _texts(bot) if "Thank you" not in m]
    assert len(parts) >= 2  # too long for one message once escaped
    for part in parts:
        _clean(part)
    rebuilt = "".join(html.unescape(p.split("\n\n", 1)[1]) for p in parts)
    assert rebuilt == body


@pytest.mark.parametrize("hostile", HOSTILE)
async def test_the_file_caption_escapes_a_hostile_name(db, hostile):
    db.execute(
        "UPDATE product_variants SET delivery_kind = 'file', delivery_file_id = 'FILE123',"
        " delivery_text = NULL WHERE id = ?",
        (TEXT,),
    )
    db.commit()
    products_repo.update_name(1, f"Pack {hostile}")
    bot = fake_bot()
    await delivery.deliver_order(bot, paid_order({TEXT: 1}))

    caption = str(bot.send_document.await_args_list[0].kwargs["caption"])
    _clean(caption)
    assert html.escape(f"Pack {hostile}") in caption


@pytest.mark.parametrize("hostile", HOSTILE)
async def test_the_receipt_escapes_hostile_item_names(db, hostile):
    products_repo.update_name(1, f"Pack {hostile}")
    variants_repo.update_label(TEXT, f"Ed {hostile}")
    bot = fake_bot()
    await delivery.deliver_order(bot, paid_order({TEXT: 1}))

    receipt = _texts(bot)[-1]
    assert "Thank you" in receipt
    _clean(receipt)
    assert html.escape(f"Pack {hostile}") in receipt and html.escape(f"Ed {hostile}") in receipt
