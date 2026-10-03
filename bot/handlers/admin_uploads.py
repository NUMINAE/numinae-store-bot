"""Owner uploads: delivery files for file editions and licence keys for key
editions. Restart-safe: what the owner is about to send is an admin_pending_input
row ('var_file' / 'var_keys'), not in-memory state.

Security properties: documents from anyone but the owner, and from the owner
without a matching pending row, store and download NOTHING (they just get a
gentle nudge). A keys .txt over the size cap is rejected BEFORE any download,
the file is downloaded into memory only (never to disk), its length is checked
again after downloading, and key text is never echoed or logged — only counts
and line numbers are reported."""

from __future__ import annotations

import logging
from typing import Any

from telegram import Message, Update
from telegram.ext import ContextTypes

from ..config import get_settings
from ..i18n import t
from ..keyboards import main_reply_keyboard
from ..keyparse import MAX_KEYS_PER_BATCH, ParsedKeys, parse_keys
from ..render import esc
from ..repo import admin_pending as pending_repo
from ..repo import license_keys as keys_repo
from ..repo import variants as variants_repo
from ..tg import require_message, require_user
from . import admin_common
from .common import get_lang

logger = logging.getLogger(__name__)

MAX_KEYS_FILE_BYTES = 262_144  # 256 KiB
MAX_INVALID_LINES_SHOWN = 10
PENDING_FILE = "var_file"
PENDING_KEYS = "var_keys"

FILE_PROMPT = (
    "Send the file for this edition as a File (use the paperclip → File, not a photo or "
    "video, so Telegram keeps it unchanged). Replacing a file updates what existing buyers "
    "can re-download."
)
KEYS_PROMPT = (
    "Send the licence keys, one per line: paste them as a message, or send a .txt file "
    f"(UTF-8, up to {MAX_KEYS_FILE_BYTES // 1024} KB). Up to {MAX_KEYS_PER_BATCH} keys per batch; "
    "duplicates are skipped."
)


async def file_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, variant_id: int) -> None:
    variant = variants_repo.get(variant_id)
    if variant is None or variant["delivery_kind"] != "file":
        await _expired(update)
        return
    pending_repo.set(require_user(update).id, PENDING_FILE, target_id=variant_id)
    await admin_common.prompt(update, context, FILE_PROMPT)


async def keys_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, variant_id: int) -> None:
    variant = variants_repo.get(variant_id)
    if variant is None or variant["delivery_kind"] != "key":
        await _expired(update)
        return
    pending_repo.set(require_user(update).id, PENDING_KEYS, target_id=variant_id)
    await admin_common.prompt(update, context, KEYS_PROMPT)


async def _expired(update: Update) -> None:
    query = update.callback_query
    if query is not None:
        await query.answer("This menu has expired.", show_alert=True)


def _is_owner(update: Update) -> bool:
    user = update.effective_user
    return user is not None and user.id == get_settings().owner_id


def _pending_for(update: Update, *actions: str) -> Any:
    """The owner's pending row if it is one of `actions`; None for anyone else."""
    if not _is_owner(update):
        return None
    pending = pending_repo.get(require_user(update).id)
    if pending is None or pending["action"] not in actions:
        return None
    return pending


async def _nudge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from .router import nudge  # late import: router imports admin_catalogue

    await nudge(update, context)


async def _finish(
    update: Update, context: ContextTypes.DEFAULT_TYPE, variant_id: int, text: str
) -> None:
    """Confirm, clear the pending row, restore the reply keyboard and show the card."""
    from . import admin_catalogue  # late import: admin_catalogue imports this module

    user_id = require_user(update).id
    pending_repo.clear(user_id)
    message = require_message(update)
    await message.reply_text(text)
    await admin_catalogue.show_variant_detail(update, context, variant_id)
    lang = get_lang(user_id)
    await context.bot.send_message(
        chat_id=user_id,
        text=t("common.menu_restored", lang),
        reply_markup=main_reply_keyboard(lang),
    )


# ---------------------------------------------------------------------------
# Key entry
# ---------------------------------------------------------------------------


def key_report(parsed: ParsedKeys, added: int) -> str:
    """Counts and line numbers only: never any key text."""
    duplicates = parsed.duplicates + (len(parsed.valid) - added)
    lines = [f"Added {added} key(s)."]
    if duplicates:
        lines.append(f"Skipped {duplicates} duplicate(s).")
    if parsed.invalid_lines:
        shown = parsed.invalid_lines[:MAX_INVALID_LINES_SHOWN]
        more = len(parsed.invalid_lines) - len(shown)
        text = ", ".join(str(n) for n in shown) + (f" (and {more} more)" if more else "")
        lines.append(f"Skipped {len(parsed.invalid_lines)} invalid line(s): {text}.")
    if parsed.over_limit:
        lines.append(f"Over the limit: only the first {MAX_KEYS_PER_BATCH} keys were used.")
    return "\n".join(lines)


async def add_keys(
    update: Update, context: ContextTypes.DEFAULT_TYPE, variant_id: int, raw: str
) -> None:
    """Parse `raw` and add the keys to the edition. With nothing valid to add the
    owner is told why and the pending input stays, so they can try again."""
    parsed = parse_keys(raw)
    if not parsed.valid:
        problems = key_report(parsed, 0)
        message = require_message(update)
        await message.reply_text(
            "No usable keys found.\n" + problems + "\nSend them again, or Cancel.",
            reply_markup=admin_common.cancel_keyboard(),
        )
        return
    added = keys_repo.add_many(variant_id, parsed.valid)
    await _finish(update, context, variant_id, key_report(parsed, added))


async def apply_pasted_keys(update, context, target_id, context_id, raw) -> None:
    """Typed-input handler for 'var_keys' (a plain text message)."""
    await add_keys(update, context, target_id, raw)


async def apply_file_text(update, context, target_id, context_id, raw) -> None:
    """Typed-input handler for 'var_file': text where a file was expected."""
    await admin_common.reprompt_after_invalid(
        update, context, "Please send the file itself, as a File (not text)."
    )


# ---------------------------------------------------------------------------
# Incoming documents
# ---------------------------------------------------------------------------


async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = require_message(update)
    document = message.document
    if document is None:
        return
    pending = _pending_for(update, PENDING_FILE, PENDING_KEYS)
    if pending is None:
        await _nudge(update, context)
        return

    variant_id = pending["target_id"]
    if pending["action"] == PENDING_FILE:
        await _store_file(update, context, variant_id, message)
    else:
        await _read_keys_file(update, context, variant_id, message)


async def _store_file(
    update: Update, context: ContextTypes.DEFAULT_TYPE, variant_id: int, message: Message
) -> None:
    document = message.document
    if document is None:
        return
    try:
        variants_repo.set_delivery_file(variant_id, document.file_id, document.file_name)
    except ValueError:
        pending_repo.clear(require_user(update).id)
        await message.reply_text("That edition no longer takes a file.")
        return
    name = document.file_name or "(unnamed)"
    await _finish(update, context, variant_id, f"File saved: {esc(name)}")


async def _read_keys_file(
    update: Update, context: ContextTypes.DEFAULT_TYPE, variant_id: int, message: Message
) -> None:
    document = message.document
    if document is None:
        return
    keyboard = admin_common.cancel_keyboard()
    name = (document.file_name or "").lower()
    if not name.endswith(".txt"):
        await message.reply_text(
            "Please send a .txt file with one key per line, or paste the keys as a message.",
            reply_markup=keyboard,
        )
        return
    if document.file_size is not None and document.file_size > MAX_KEYS_FILE_BYTES:
        # Rejected on the size Telegram reports, before anything is downloaded.
        await message.reply_text(
            f"That file is too large (limit {MAX_KEYS_FILE_BYTES // 1024} KB). "
            "Split the keys into smaller files.",
            reply_markup=keyboard,
        )
        return

    try:
        telegram_file = await context.bot.get_file(document.file_id)
        data = await telegram_file.download_as_bytearray()  # into memory, never to disk
    except Exception:
        logger.error("Could not download a keys file (variant_id=%s)", variant_id, exc_info=True)
        await message.reply_text(
            "Could not download that file from Telegram. Try again.", reply_markup=keyboard
        )
        return
    if len(data) > MAX_KEYS_FILE_BYTES:  # the reported size can be absent or wrong
        await message.reply_text(
            f"That file is too large (limit {MAX_KEYS_FILE_BYTES // 1024} KB).",
            reply_markup=keyboard,
        )
        return
    try:
        text = bytes(data).decode("utf-8-sig")
    except UnicodeDecodeError:
        await message.reply_text(
            "That file isn't valid UTF-8 text. Save it as UTF-8 and send it again.",
            reply_markup=keyboard,
        )
        return
    await add_keys(update, context, variant_id, text)


# ---------------------------------------------------------------------------
# Other media while a file/keys prompt is open
# ---------------------------------------------------------------------------


async def on_other_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A photo, video, audio, voice message or sticker. Nothing is ever stored
    from these. While the owner has a file or keys prompt open they are told how
    to send the right thing; otherwise the update is ignored."""
    message = require_message(update)
    pending = _pending_for(update, PENDING_FILE, PENDING_KEYS)
    if pending is None:
        return
    if pending["action"] == PENDING_FILE:
        text = (
            "That was sent as media, which Telegram recompresses. Please send it again as a "
            "File (paperclip → File)."
        )
    else:
        text = "Please paste the keys as text, or send a .txt file."
    await message.reply_text(text, reply_markup=admin_common.cancel_keyboard())
