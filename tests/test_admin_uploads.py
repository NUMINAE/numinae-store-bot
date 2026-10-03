"""Owner uploads: delivery files for file editions and licence keys for key
editions, through the real handlers with fake updates and a fake bot."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from bot.handlers import admin_catalogue, admin_uploads, router
from bot.repo import admin_pending as pending_repo
from bot.repo import categories as categories_repo
from bot.repo import license_keys as keys_repo
from bot.repo import products as products_repo
from bot.repo import variants as variants_repo

from .conftest import OWNER_ID
from .flow import Flow
from .helpers import add_customer, make_message, make_update

STRANGER = 2002
HOSTILE = "<b>&"


@pytest.fixture
def flow(db, monkeypatch):
    add_customer(OWNER_ID)
    return Flow(monkeypatch, OWNER_ID)


@pytest.fixture
def editions(db):
    product = products_repo.create(categories_repo.create("Uploads"), "Product")
    return {
        "file": variants_repo.create(product, "File ed", 10, "file"),
        "key": variants_repo.create(product, "Key ed", 10, "key"),
        "text": variants_repo.create(product, "Text ed", 10, "text"),
    }


def document(name="keys.txt", size=100, file_id="FILE_ID_1", mime="text/plain") -> Any:
    return SimpleNamespace(file_id=file_id, file_name=name, file_size=size, mime_type=mime)


def serve(flow: Flow, content: bytes | str) -> None:
    """Make the fake bot's get_file/download return `content`."""
    data = content.encode("utf-8") if isinstance(content, str) else content
    telegram_file = SimpleNamespace(download_as_bytearray=AsyncMock(return_value=bytearray(data)))
    flow.context.bot.get_file = AsyncMock(return_value=telegram_file)
    flow.served = telegram_file  # type: ignore[attr-defined]  # inspected by the tests


async def send_document(flow: Flow, doc: Any, user_id: int = OWNER_ID):
    message = make_message(None)
    message.document = doc
    update = make_update(user_id, message=message, chat_id=user_id)
    await admin_uploads.on_document(update, flow.context)
    return message


async def send_media(flow: Flow, user_id: int = OWNER_ID):
    message = make_message(None)
    update = make_update(user_id, message=message, chat_id=user_id)
    await admin_uploads.on_other_media(update, flow.context)
    return message


def replies(message) -> list[str]:
    return [str(c.args[0]) for c in message.reply_text.await_args_list]


def key_text(*keys: str) -> str:
    return "\n".join(keys)


# --- the edition card -------------------------------------------------------------------------


async def test_a_file_edition_card_offers_upload_then_replace_with_the_escaped_name(flow, editions):
    variant = editions["file"]
    await flow.tap(admin_catalogue.route, f"adm:va:v:{variant}")
    assert "File: no file yet" in flow.text and "Upload file" in flow.labels()
    assert "Hidden from customers: no delivery content yet (upload the file)" in flow.text

    variants_repo.set_delivery_file(variant, "FID", HOSTILE + ".zip")
    await flow.tap(admin_catalogue.route, f"adm:va:v:{variant}")
    assert "File: &lt;b&gt;&amp;.zip" in flow.text and "<b>&.zip" not in flow.text
    assert "Replace file" in flow.labels() and "Upload file" not in flow.labels()
    assert "existing buyers can re-download" in flow.text
    assert "Hidden from customers" not in flow.text


async def test_a_key_edition_card_shows_all_four_stock_counts_and_add_keys(flow, editions):
    variant = editions["key"]
    keys_repo.add_many(variant, ["A", "B", "C", "D", "E"])
    from bot.db import get_conn

    get_conn().execute(
        "UPDATE license_keys SET status = 'reserved' WHERE key_value = 'B' AND variant_id = ?",
        (variant,),
    )
    get_conn().execute(
        "UPDATE license_keys SET status = 'sold' WHERE key_value = 'C' AND variant_id = ?",
        (variant,),
    )
    get_conn().execute(
        "UPDATE license_keys SET status = 'revoked' WHERE key_value = 'D' AND variant_id = ?",
        (variant,),
    )
    get_conn().commit()
    await flow.tap(admin_catalogue.route, f"adm:va:v:{variant}")
    assert "Keys: 2 available / 1 reserved / 1 sold / 1 revoked" in flow.text
    assert "Add keys" in flow.labels()
    assert "Hidden from customers" not in flow.text  # there is stock


async def test_a_key_edition_without_stock_keeps_its_warning(flow, editions):
    await flow.tap(admin_catalogue.route, f"adm:va:v:{editions['key']}")
    assert "Hidden from customers: no delivery content yet (no keys in stock)" in flow.text


async def test_text_editions_get_neither_upload_nor_add_keys(flow, editions):
    await flow.tap(admin_catalogue.route, f"adm:va:v:{editions['text']}")
    assert "Upload file" not in flow.labels() and "Add keys" not in flow.labels()


async def test_the_prompts_say_what_to_send_and_offer_cancel(flow, editions):
    await flow.tap(admin_catalogue.route, f"adm:va:uf:{editions['file']}")
    assert "as a File" in flow.text and "re-download" in flow.text and "Cancel" in flow.labels()
    assert pending_repo.get(OWNER_ID)["action"] == "var_file"
    await flow.tap(admin_catalogue.route, "adm:cancel")
    assert pending_repo.get(OWNER_ID) is None

    await flow.tap(admin_catalogue.route, f"adm:va:ak:{editions['key']}")
    assert "one per line" in flow.text and ".txt" in flow.text and "Cancel" in flow.labels()
    assert pending_repo.get(OWNER_ID)["action"] == "var_keys"
    assert pending_repo.get(OWNER_ID)["target_id"] == editions["key"]  # restart-safe: in the DB


@pytest.mark.parametrize(
    ("route", "wrong_kind"), [("uf", "key"), ("uf", "text"), ("ak", "file"), ("ak", "text")]
)
async def test_the_prompts_refuse_the_wrong_kind_of_edition(flow, editions, route, wrong_kind):
    await flow.tap(admin_catalogue.route, f"adm:va:{route}:{editions[wrong_kind]}")
    assert flow.alert == "This menu has expired."
    assert pending_repo.get(OWNER_ID) is None


# --- file upload ------------------------------------------------------------------------------


async def test_a_file_upload_stores_id_and_name_confirms_and_downloads_nothing(flow, editions):
    variant = editions["file"]
    await flow.tap(admin_catalogue.route, f"adm:va:uf:{variant}")
    serve(flow, b"never read")

    message = await send_document(
        flow, document("guide.pdf", 5000, "TELEGRAM_FILE_ID", "application/pdf")
    )

    row = variants_repo.get(variant)
    assert (row["delivery_file_id"], row["delivery_file_name"]) == ("TELEGRAM_FILE_ID", "guide.pdf")
    assert replies(message) == ["File saved: guide.pdf"]
    assert pending_repo.get(OWNER_ID) is None
    flow.context.bot.get_file.assert_not_called()  # nothing is downloaded
    assert "File: guide.pdf" in flow.text or any(
        "guide.pdf" in str(c.kwargs.get("text", "")) for c in flow.sent_messages()
    )
    # the reply keyboard comes back
    restored = flow.sent_messages()[-1].kwargs
    assert restored["reply_markup"].keyboard[0][0].text == "Browse Products"


async def test_replacing_a_file_overwrites_the_stored_id_and_name(flow, editions):
    variant = editions["file"]
    variants_repo.set_delivery_file(variant, "OLD", "old.zip")
    await flow.tap(admin_catalogue.route, f"adm:va:uf:{variant}")
    await send_document(flow, document("new.zip", 10, "NEW"))
    row = variants_repo.get(variant)
    assert (row["delivery_file_id"], row["delivery_file_name"]) == ("NEW", "new.zip")


async def test_an_unnamed_file_is_stored_with_no_name(flow, editions):
    variant = editions["file"]
    await flow.tap(admin_catalogue.route, f"adm:va:uf:{variant}")
    message = await send_document(flow, document(None, None, "FID"))
    assert variants_repo.get(variant)["delivery_file_name"] is None
    assert replies(message) == ["File saved: (unnamed)"]


async def test_a_hostile_file_name_is_escaped_in_the_confirmation(flow, editions):
    variant = editions["file"]
    await flow.tap(admin_catalogue.route, f"adm:va:uf:{variant}")
    message = await send_document(flow, document(HOSTILE + ".zip", 5, "FID"))
    assert replies(message) == ["File saved: &lt;b&gt;&amp;.zip"]
    assert variants_repo.get(variant)["delivery_file_name"] == HOSTILE + ".zip"  # stored raw


async def test_text_sent_while_a_file_is_expected_is_refused_and_the_prompt_stays(flow, editions):
    await flow.tap(admin_catalogue.route, f"adm:va:uf:{editions['file']}")
    await flow.send(router.route_text, "here is my file, honest")
    assert "send the file itself" in flow.replies[-1].lower()
    assert pending_repo.get(OWNER_ID)["action"] == "var_file"
    assert variants_repo.get(editions["file"])["delivery_file_id"] is None


async def test_the_edition_vanishing_before_the_file_arrives_is_handled(flow, editions):
    variant = editions["text"]  # not a file edition any more / never was
    pending_repo.set(OWNER_ID, "var_file", target_id=variant)
    message = await send_document(flow, document("a.zip", 5, "FID"))
    assert replies(message) == ["That edition no longer takes a file."]
    assert pending_repo.get(OWNER_ID) is None
    assert variants_repo.get(variant)["delivery_file_id"] is None


# --- pasted keys ------------------------------------------------------------------------------


async def test_pasted_keys_are_added_and_reported_by_count(flow, editions, db):
    variant = editions["key"]
    keys_repo.add_many(variant, ["EXIST-1"])
    await flow.tap(admin_catalogue.route, f"adm:va:ak:{variant}")

    await flow.send(router.route_text, key_text("K-1", "K-2", "K-2", "EXIST-1", "", "bad\x00key"))

    assert keys_repo.count_by_status(variant)["available"] == 3  # EXIST-1, K-1, K-2
    report = flow.replies[-1]
    assert "Added 2 key(s)." in report
    assert "Skipped 2 duplicate(s)." in report  # one in the batch, one already in stock
    assert "Skipped 1 invalid line(s): 6." in report
    assert pending_repo.get(OWNER_ID) is None


async def test_key_text_is_never_echoed_or_logged(flow, editions, caplog):
    caplog.set_level(logging.DEBUG)
    variant = editions["key"]
    await flow.tap(admin_catalogue.route, f"adm:va:ak:{variant}")
    await flow.send(router.route_text, key_text("TOPSECRET-1", "TOPSECRET-1", "TOPSECRET\x00bad"))
    everything = (
        "\n".join(flow.replies) + caplog.text + str(flow.context.bot.send_message.mock_calls)
    )
    assert "TOPSECRET" not in everything


async def test_hostile_key_text_is_stored_raw_and_never_rendered_unescaped(flow, editions):
    variant = editions["key"]
    await flow.tap(admin_catalogue.route, f"adm:va:ak:{variant}")
    await flow.send(router.route_text, HOSTILE)
    assert keys_repo.count_available(variant) == 1
    assert HOSTILE not in "\n".join(flow.replies)


async def test_only_ten_invalid_line_numbers_are_listed(flow, editions):
    variant = editions["key"]
    await flow.tap(admin_catalogue.route, f"adm:va:ak:{variant}")
    lines = ["OK"] + [f"bad{n}\x00" for n in range(15)]
    await flow.send(router.route_text, "\n".join(lines))
    report = flow.replies[-1]
    assert "Skipped 15 invalid line(s): 2, 3, 4, 5, 6, 7, 8, 9, 10, 11 (and 5 more)." in report


async def test_a_batch_over_the_limit_reports_it(flow, editions):
    variant = editions["key"]
    await flow.tap(admin_catalogue.route, f"adm:va:ak:{variant}")
    await flow.send(router.route_text, "\n".join(f"K{n}" for n in range(5001)))
    assert "Over the limit: only the first 5000 keys were used." in flow.replies[-1]
    assert keys_repo.count_available(variant) == 5000


async def test_nothing_usable_keeps_the_prompt_open(flow, editions):
    variant = editions["key"]
    await flow.tap(admin_catalogue.route, f"adm:va:ak:{variant}")
    await flow.send(router.route_text, key_text("bad\x00", "   ", "worse\x01"))
    assert "No usable keys found." in flow.replies[-1]
    assert "invalid line(s): 1, 3" in flow.replies[-1]
    assert pending_repo.get(OWNER_ID)["action"] == "var_keys"
    assert keys_repo.count_available(variant) == 0


# --- .txt key files ---------------------------------------------------------------------------


async def test_a_txt_file_of_keys_is_downloaded_into_memory_parsed_and_added(flow, editions):
    variant = editions["key"]
    await flow.tap(admin_catalogue.route, f"adm:va:ak:{variant}")
    serve(flow, "﻿A-1\r\nA-2\r\nA-2\r\n\r\nüñí-3\r\n")

    message = await send_document(flow, document("keys.TXT", 40, "KEYFILE"))

    flow.context.bot.get_file.assert_awaited_once_with("KEYFILE")
    flow.served.download_as_bytearray.assert_awaited_once_with()  # into memory: no path given
    assert keys_repo.count_available(variant) == 3
    assert replies(message) == ["Added 3 key(s).\nSkipped 1 duplicate(s)."]
    assert pending_repo.get(OWNER_ID) is None


async def test_an_oversized_txt_is_rejected_before_any_download(flow, editions):
    variant = editions["key"]
    await flow.tap(admin_catalogue.route, f"adm:va:ak:{variant}")
    serve(flow, "would-be-keys")

    message = await send_document(flow, document("big.txt", admin_uploads.MAX_KEYS_FILE_BYTES + 1))

    flow.context.bot.get_file.assert_not_called()  # no get_file...
    flow.served.download_as_bytearray.assert_not_called()  # ...and no download
    assert "too large" in replies(message)[0] and "256 KB" in replies(message)[0]
    assert keys_repo.count_available(variant) == 0
    assert pending_repo.get(OWNER_ID)["action"] == "var_keys"  # still waiting


async def test_a_txt_exactly_at_the_size_cap_is_accepted(flow, editions):
    variant = editions["key"]
    await flow.tap(admin_catalogue.route, f"adm:va:ak:{variant}")
    serve(flow, "ONLY-KEY")
    await send_document(flow, document("cap.txt", admin_uploads.MAX_KEYS_FILE_BYTES))
    assert keys_repo.count_available(variant) == 1


async def test_the_downloaded_length_is_rechecked_when_the_reported_size_lied(flow, editions):
    variant = editions["key"]
    await flow.tap(admin_catalogue.route, f"adm:va:ak:{variant}")
    serve(flow, b"x" * (admin_uploads.MAX_KEYS_FILE_BYTES + 1))
    message = await send_document(flow, document("liar.txt", 10))  # claims 10 bytes
    assert "too large" in replies(message)[0]
    assert keys_repo.count_available(variant) == 0


async def test_a_txt_with_no_reported_size_is_downloaded_then_checked(flow, editions):
    variant = editions["key"]
    await flow.tap(admin_catalogue.route, f"adm:va:ak:{variant}")
    serve(flow, "SIZELESS-1")
    await send_document(flow, document("nosize.txt", None))
    assert keys_repo.count_available(variant) == 1


async def test_a_file_that_is_not_utf8_gets_a_friendly_message(flow, editions):
    variant = editions["key"]
    await flow.tap(admin_catalogue.route, f"adm:va:ak:{variant}")
    serve(flow, b"\xff\xfe\x00bad bytes \x80")
    message = await send_document(flow, document("latin.txt", 20))
    assert "isn't valid UTF-8" in replies(message)[0]
    assert keys_repo.count_available(variant) == 0
    assert pending_repo.get(OWNER_ID)["action"] == "var_keys"


async def test_a_download_failure_is_reported_and_logged_without_key_data(flow, editions, caplog):
    caplog.set_level(logging.ERROR)
    variant = editions["key"]
    await flow.tap(admin_catalogue.route, f"adm:va:ak:{variant}")
    flow.context.bot.get_file = AsyncMock(side_effect=RuntimeError("telegram down"))
    message = await send_document(flow, document("keys.txt", 20))
    assert "Could not download" in replies(message)[0]
    assert any(r.levelno == logging.ERROR for r in caplog.records)
    assert pending_repo.get(OWNER_ID) is not None


@pytest.mark.parametrize("name", ["keys.csv", "keys", "keys.txt.exe", None, "notes.pdf"])
async def test_only_txt_documents_are_read_as_keys(flow, editions, name):
    variant = editions["key"]
    await flow.tap(admin_catalogue.route, f"adm:va:ak:{variant}")
    serve(flow, "SHOULD-NOT-BE-READ")
    message = await send_document(flow, document(name, 10))
    assert "send a .txt file" in replies(message)[0]
    flow.context.bot.get_file.assert_not_called()
    assert keys_repo.count_available(variant) == 0


# --- who may upload ---------------------------------------------------------------------------


async def test_a_non_owner_document_is_ignored_and_nothing_is_stored_or_downloaded(
    flow, editions, db, monkeypatch
):
    variant = editions["key"]
    await flow.tap(admin_catalogue.route, f"adm:va:ak:{variant}")  # the OWNER has a prompt open
    add_customer(STRANGER)
    serve(flow, "STRANGER-KEY-1")
    pending_before = dict(pending_repo.get(OWNER_ID))
    snapshot = list(db.iterdump())

    for doc in (document("keys.txt", 10), document("evil.zip", 10, "EVIL_FILE")):
        message = await send_document(flow, doc, user_id=STRANGER)
        (reply,) = replies(message)  # just the gentle nudge, nothing about uploads
        assert "menu" in reply.lower() and "keys" not in reply.lower().split("menu")[0]
    flow.context.bot.get_file.assert_not_called()  # nothing downloaded
    assert list(db.iterdump()) == snapshot  # nothing stored anywhere
    assert dict(pending_repo.get(OWNER_ID)) == pending_before  # the owner's prompt is untouched


async def test_a_non_owner_gets_a_nudge_not_an_error(flow, editions):
    add_customer(STRANGER)
    message = await send_document(flow, document("keys.txt", 10), user_id=STRANGER)
    (call,) = message.reply_text.await_args_list
    assert "menu" in call.args[0].lower()


async def test_the_owner_without_a_pending_prompt_gets_a_nudge_and_nothing_is_stored(
    flow, editions, db
):
    serve(flow, "KEY-NOT-PENDING")
    snapshot = list(db.iterdump())
    message = await send_document(flow, document("keys.txt", 10))
    assert "menu" in message.reply_text.await_args.args[0].lower()
    flow.context.bot.get_file.assert_not_called()
    assert list(db.iterdump()) == snapshot


async def test_an_unrelated_pending_action_does_not_make_a_document_acceptable(flow, editions):
    pending_repo.set(OWNER_ID, "var_edit_label", target_id=editions["key"])
    serve(flow, "KEY-X")
    message = await send_document(flow, document("keys.txt", 10))
    assert "menu" in message.reply_text.await_args.args[0].lower()
    flow.context.bot.get_file.assert_not_called()
    assert keys_repo.count_available(editions["key"]) == 0


async def test_a_message_without_a_document_is_ignored(flow):
    message = make_message(None)
    await admin_uploads.on_document(make_update(OWNER_ID, message=message), flow.context)
    message.reply_text.assert_not_called()


# --- other media while a prompt is open -------------------------------------------------------


@pytest.mark.parametrize("media", ["photo", "video", "audio", "voice", "sticker"])
async def test_other_media_while_a_file_is_pending_gets_the_send_as_file_hint(
    flow, editions, media
):
    variant = editions["file"]
    await flow.tap(admin_catalogue.route, f"adm:va:uf:{variant}")
    message = await send_media(flow)
    assert "send it again as a File" in replies(message)[0]
    assert variants_repo.get(variant)["delivery_file_id"] is None  # nothing stored
    assert pending_repo.get(OWNER_ID)["action"] == "var_file"  # still waiting
    flow.context.bot.get_file.assert_not_called()
    del media  # one handler serves all five types; the registration test pins the filters


async def test_media_while_keys_are_pending_says_to_paste_or_send_txt(flow, editions):
    await flow.tap(admin_catalogue.route, f"adm:va:ak:{editions['key']}")
    message = await send_media(flow)
    assert "paste the keys as text" in replies(message)[0]


async def test_media_from_a_non_owner_or_without_a_prompt_is_silently_ignored(flow, editions):
    add_customer(STRANGER)
    await flow.tap(admin_catalogue.route, f"adm:va:uf:{editions['file']}")
    stranger_message = await send_media(flow, user_id=STRANGER)
    assert replies(stranger_message) == []
    pending_repo.clear(OWNER_ID)
    owner_message = await send_media(flow)
    assert replies(owner_message) == []


def test_the_media_handler_is_registered_for_photo_video_audio_voice_and_sticker(
    monkeypatch, tmp_path
):
    from telegram import Chat, Message
    from telegram.ext import MessageHandler

    from bot import db as db_module
    from bot import main
    from bot.config import get_settings

    db_module.close_conn()
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    get_settings.cache_clear()
    application = main.build_application()
    handler = next(
        h
        for group in application.handlers.values()
        for h in group
        if isinstance(h, MessageHandler) and h.callback is admin_uploads.on_other_media
    )
    doc_handler = next(
        h
        for group in application.handlers.values()
        for h in group
        if isinstance(h, MessageHandler) and h.callback is admin_uploads.on_document
    )
    from datetime import UTC, datetime

    from telegram import Audio, Document, PhotoSize, Sticker, Update, Video, Voice

    chat = Chat(1, "private")

    def update_with(**kwargs: Any) -> Update:
        message = Message(1, datetime.now(UTC), chat, **kwargs)
        return Update(1, message=message)

    samples = {
        "photo": update_with(photo=(PhotoSize("a", "b", 1, 1),)),
        "video": update_with(video=Video("a", "b", 1, 1, 1)),
        "audio": update_with(audio=Audio("a", "b", 1)),
        "voice": update_with(voice=Voice("a", "b", 1)),
        "sticker": update_with(sticker=Sticker("a", "b", 1, 1, "regular", False, False)),
    }
    for name, update in samples.items():
        assert handler.check_update(update), name
        assert not doc_handler.check_update(update), name
    document_update = update_with(document=Document("a", "b"))
    assert doc_handler.check_update(document_update)
    assert not handler.check_update(document_update)
    db_module.close_conn()
    get_settings.cache_clear()
