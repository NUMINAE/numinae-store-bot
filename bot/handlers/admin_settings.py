"""The settings editor: welcome/shipping/support text, order expiry hours,
and the reminder-percent schedule Phase 5 will consume. Registered under the
"adm:set:" callback namespace.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from ..keyboards import cb
from ..render import bold, esc
from ..repo import admin_pending as pending_repo
from ..repo import settings as settings_repo
from ..ui import show_screen
from . import admin_common

SETTING_LABELS: dict[str, str] = {
    "welcome_text": "Welcome text",
    "shipping_note": "Shipping note",
    "support_note": "Support note",
    "order_expiry_hours": "Order expiry hours",
    "reminder_percents": "Reminder schedule",
}

_PREVIEW_LEN = 40


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_long_text(text: str, *, allow_clear: bool) -> tuple[bool, str | None, str]:
    cleaned = text.strip()
    if allow_clear and cleaned == "-":
        return True, "", "(cleared)"
    if not cleaned or len(cleaned) > 1000:
        return False, None, "Please send between 1 and 1000 characters."
    preview = cleaned if len(cleaned) <= 60 else cleaned[:57] + "..."
    return True, cleaned, preview


def _validate_expiry_hours(text: str) -> tuple[bool, str | None, str]:
    cleaned = text.strip()
    try:
        value = int(cleaned)
    except ValueError:
        return False, None, "Please send a whole number of hours between 1 and 168."
    if not (1 <= value <= 168):
        return False, None, "Please send a whole number of hours between 1 and 168."
    return True, str(value), str(value)


def _validate_reminder_percents(text: str) -> tuple[bool, str | None, str]:
    cleaned = text.strip()
    parts = [p.strip() for p in cleaned.split(",")]
    if not (1 <= len(parts) <= 5):
        return False, None, 'Send 1 to 5 comma-separated percents, e.g. "25,50,83".'

    values = []
    for p in parts:
        try:
            v = int(p)
        except ValueError:
            return False, None, "Each value must be a whole number between 1 and 99."
        if not (1 <= v <= 99):
            return False, None, "Each value must be a whole number between 1 and 99."
        values.append(v)

    if any(values[i] >= values[i + 1] for i in range(len(values) - 1)):
        return False, None, 'Values must be strictly ascending, e.g. "25,50,83".'

    normalized = ",".join(str(v) for v in values)
    return True, normalized, normalized


# ---------------------------------------------------------------------------
# Screens
# ---------------------------------------------------------------------------


def _notify_new_customers_enabled() -> bool:
    return settings_repo.get("notify_new_customers", "1") == "1"


async def show_settings_root(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = settings_repo.list_editable()
    body = [bold("Settings"), ""]
    for row in rows:
        label = SETTING_LABELS.get(row["key"], row["key"])
        preview = row["value"]
        if len(preview) > _PREVIEW_LEN:
            preview = preview[: _PREVIEW_LEN - 3] + "..."
        body.append(f"{label}: {esc(preview) if preview else '(empty)'}")
    notify_on = _notify_new_customers_enabled()
    body.append(f"New customer notifications: {'ON' if notify_on else 'OFF'}")
    text = "\n".join(body)

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Welcome text", callback_data=cb("adm", "set", "v", "welcome_text")
                ),
                InlineKeyboardButton(
                    "Shipping note", callback_data=cb("adm", "set", "v", "shipping_note")
                ),
                InlineKeyboardButton(
                    "Support note", callback_data=cb("adm", "set", "v", "support_note")
                ),
            ],
            [
                InlineKeyboardButton(
                    "Order expiry hours", callback_data=cb("adm", "set", "v", "order_expiry_hours")
                ),
                InlineKeyboardButton(
                    "Reminder schedule", callback_data=cb("adm", "set", "v", "reminder_percents")
                ),
            ],
            [
                InlineKeyboardButton(
                    f"New customer notifications: {'Turn OFF' if notify_on else 'Turn ON'}",
                    callback_data=cb("adm", "set", "tgnc"),
                )
            ],
            [InlineKeyboardButton("Back to admin", callback_data=cb("adm", "home"))],
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
    text = f"{bold(label)}\n\n{esc(value) if value else '(empty)'}"
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Edit", callback_data=cb("adm", "set", "e", key))],
            [InlineKeyboardButton("Back to settings", callback_data=cb("adm", "set"))],
        ]
    )
    await show_screen(update, context, text=text, keyboard=keyboard)


def _reminder_example(expiry_hours: int) -> str:
    example_percents = [25, 50, 83]
    parts = []
    for pct in example_percents:
        hours = expiry_hours * pct / 100
        parts.append(f"{hours:g}h")
    return ", ".join(parts)


async def action_edit_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str) -> None:
    if key not in SETTING_LABELS:
        await update.callback_query.answer("Unknown setting.", show_alert=True)
        return

    if key == "reminder_percents":
        expiry = int(settings_repo.get("order_expiry_hours", "24") or "24")
        text = (
            "Send 1 to 5 comma-separated percents (each 1–99, strictly ascending) of the "
            "order's expiry time to send payment reminders at.\n\n"
            f'Example: "25,50,83" with your current {expiry} hour expiry sends reminders at '
            f"{_reminder_example(expiry)}."
        )
    elif key == "order_expiry_hours":
        text = "Send the number of hours an unpaid order stays open before expiring (1–168)."
    elif key in ("shipping_note", "support_note"):
        text = (
            f"Send the new {SETTING_LABELS[key].lower()} (1–1000 characters). "
            'Send a single "-" to clear it.'
        )
    else:  # welcome_text
        text = "Send the new welcome text (1–1000 characters). This cannot be empty."

    pending_repo.set(update.effective_user.id, "set_value", target_key=key)
    await admin_common.prompt(update, context, text)


async def apply_set_value(update, context, target_key, context_id, raw) -> None:
    key = target_key
    if key == "welcome_text":
        ok, value, message = _validate_long_text(raw, allow_clear=False)
    elif key in ("shipping_note", "support_note"):
        ok, value, message = _validate_long_text(raw, allow_clear=True)
    elif key == "order_expiry_hours":
        ok, value, message = _validate_expiry_hours(raw)
    elif key == "reminder_percents":
        ok, value, message = _validate_reminder_percents(raw)
    else:
        pending_repo.clear(update.effective_user.id)
        await update.message.reply_text("Unknown setting.")
        return

    if not ok:
        await admin_common.reprompt_after_invalid(update, context, message)
        return

    settings_repo.set(key, value)
    pending_repo.clear(update.effective_user.id)
    await update.message.reply_text(f"{SETTING_LABELS.get(key, key)} updated.")
    await show_setting_detail(update, context, key)


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


async def route(update: Update, context: ContextTypes.DEFAULT_TYPE, parts: list[str]) -> None:
    query = update.callback_query
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
