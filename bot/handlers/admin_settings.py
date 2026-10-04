"""The settings editor: welcome text, support note and the reservation window,
plus the new-customer notification toggle. Registered under the "adm:set:"
callback namespace.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from ..keyboards import cb
from ..render import esc
from ..repo import admin_pending as pending_repo
from ..repo import settings as settings_repo
from ..tg import require_message, require_query, require_user
from ..ui import show_screen
from ..ui_kit import ICON, nav_row, screen, trim
from . import admin_common
from .admin_common import Invalid, Valid

SETTING_LABELS: dict[str, str] = {
    "welcome_text": "Welcome text",
    "support_note": "Support note",
    "reservation_minutes": "Reservation minutes",
}

MIN_RESERVATION_MINUTES = 5
MAX_RESERVATION_MINUTES = 60
_PREVIEW_LEN = 40
DETAIL_BUDGET = 700  # raw characters; all-"&" still fits one message


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_long_text(text: str, *, allow_clear: bool) -> Valid[str] | Invalid:
    cleaned = text.strip()
    if allow_clear and cleaned == "-":
        return Valid("", "(cleared)")
    if not cleaned or len(cleaned) > 1000:
        return Invalid("Please send between 1 and 1000 characters.")
    preview = cleaned if len(cleaned) <= 60 else cleaned[:57] + "..."
    return Valid(cleaned, preview)


def validate_reservation_minutes(text: str) -> Valid[str] | Invalid:
    problem = (
        f"Please send a whole number of minutes between "
        f"{MIN_RESERVATION_MINUTES} and {MAX_RESERVATION_MINUTES}."
    )
    cleaned = text.strip()
    if not cleaned.isascii() or not cleaned.isdigit():
        return Invalid(problem)
    value = int(cleaned)
    if not (MIN_RESERVATION_MINUTES <= value <= MAX_RESERVATION_MINUTES):
        return Invalid(problem)
    return Valid(str(value), str(value))


# ---------------------------------------------------------------------------
# Screens
# ---------------------------------------------------------------------------


def _notify_new_customers_enabled() -> bool:
    return settings_repo.get("notify_new_customers", "1") == "1"


async def show_settings_root(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = settings_repo.list_editable()
    body: list[str] = []
    for row in rows:
        label = SETTING_LABELS.get(row["key"], row["key"])
        preview = row["value"]
        if len(preview) > _PREVIEW_LEN:
            preview = preview[: _PREVIEW_LEN - 3] + "..."
        body.append(f"{label}: {esc(preview) if preview else '(empty)'}")
    notify_on = _notify_new_customers_enabled()
    body.append(f"New customer notifications: {'ON' if notify_on else 'OFF'}")
    text = screen(ICON.settings, "Settings", "\n".join(body), crumbs=["Admin"])

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Welcome text", callback_data=cb("adm", "set", "v", "welcome_text")
                ),
                InlineKeyboardButton(
                    "Support note", callback_data=cb("adm", "set", "v", "support_note")
                ),
            ],
            [
                InlineKeyboardButton(
                    "Reservation minutes",
                    callback_data=cb("adm", "set", "v", "reservation_minutes"),
                ),
            ],
            [
                InlineKeyboardButton(
                    f"New customer notifications: {'Turn OFF' if notify_on else 'Turn ON'}",
                    callback_data=cb("adm", "set", "tgnc"),
                )
            ],
            nav_row(back=cb("adm", "home")),
        ]
    )
    await show_screen(update, context, text=text, keyboard=keyboard)


async def action_toggle_notify_new_customers(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    settings_repo.set("notify_new_customers", "0" if _notify_new_customers_enabled() else "1")
    await show_settings_root(update, context)


async def show_setting_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str) -> None:
    if key not in SETTING_LABELS:
        await show_settings_root(update, context)
        return
    value = settings_repo.get(key, "")
    label = SETTING_LABELS[key]
    body = esc(trim(value, DETAIL_BUDGET)) if value else "(empty)"
    text = screen(ICON.settings, label, body, crumbs=["Settings"])
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Edit", callback_data=cb("adm", "set", "e", key))],
            nav_row(back=cb("adm", "set")),
        ]
    )
    await show_screen(update, context, text=text, keyboard=keyboard)


async def action_edit_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str) -> None:
    if key not in SETTING_LABELS:
        await require_query(update).answer("Unknown setting.", show_alert=True)
        return

    if key == "reservation_minutes":
        text = (
            "Send how many minutes a customer's licence keys stay reserved while they pay "
            f"({MIN_RESERVATION_MINUTES}–{MAX_RESERVATION_MINUTES})."
        )
    elif key == "support_note":
        text = (
            f"Send the new {SETTING_LABELS[key].lower()} (1–1000 characters). "
            'Send a single "-" to clear it.'
        )
    else:  # welcome_text
        text = "Send the new welcome text (1–1000 characters). This cannot be empty."

    pending_repo.set(require_user(update).id, "set_value", target_key=key)
    await admin_common.prompt(update, context, esc(text), title=SETTING_LABELS[key])


async def apply_set_value(update, context, target_key, context_id, raw) -> None:
    key = target_key
    result: Valid[str] | Invalid
    if key == "welcome_text":
        result = validate_long_text(raw, allow_clear=False)
    elif key == "support_note":
        result = validate_long_text(raw, allow_clear=True)
    elif key == "reservation_minutes":
        result = validate_reservation_minutes(raw)
    else:
        pending_repo.clear(require_user(update).id)
        await require_message(update).reply_text("Unknown setting.")
        return

    if isinstance(result, Invalid):
        await admin_common.reprompt_after_invalid(update, context, result.message)
        return

    settings_repo.set(key, result.value)
    pending_repo.clear(require_user(update).id)
    await require_message(update).reply_text(f"{SETTING_LABELS.get(key, key)} updated.")
    await show_setting_detail(update, context, key)


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


async def route(update: Update, context: ContextTypes.DEFAULT_TYPE, parts: list[str]) -> None:
    query = require_query(update)
    if len(parts) == 2:
        await show_settings_root(update, context)
        return
    action = parts[2]
    if action == "tgnc":
        await action_toggle_notify_new_customers(update, context)
        return
    if len(parts) < 4:
        await query.answer("This menu has expired.", show_alert=True)
        return
    key = parts[3]
    if action == "v":
        await show_setting_detail(update, context, key)
    elif action == "e":
        await action_edit_prompt(update, context, key)
    else:
        await query.answer("This menu has expired.", show_alert=True)
