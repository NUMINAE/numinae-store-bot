"""The payment methods editor. Deliberately slower and more paranoid than the
catalogue editor: setting a wallet address is always name -> light-validate
-> confirm-with-the-address-shown-back, and only the Confirm tap writes to
the database. A wrong address here is unrecoverable, so convenience loses to
carefulness throughout this module.

Registered under the "adm:pm:" callback namespace.
"""

from __future__ import annotations

import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from ..keyboards import cb
from ..render import bold, esc
from ..render import code as render_code
from ..repo import admin_pending as pending_repo
from ..repo import orders as orders_repo
from ..repo import payment_methods as payment_methods_repo
from ..ui import show_screen
from . import admin_common

# Per-asset address-format patterns, deliberately loose — a routing hint, not
# strict validation. Token methods validate against their underlying chain,
# not the token, via _METHOD_CHAIN_OVERRIDE.
_CHAIN_PATTERNS: dict[str, re.Pattern] = {
    "BTC": re.compile(r"^(bc1[a-z0-9]{25,62}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})$"),
    "LTC": re.compile(r"^(ltc1[a-z0-9]{25,62}|[LM][a-km-zA-HJ-NP-Z1-9]{26,33})$"),
    "ETH": re.compile(r"^0x[a-fA-F0-9]{40}$"),
    "BNB": re.compile(r"^0x[a-fA-F0-9]{40}$"),
    "SOL": re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$"),
    "TRX": re.compile(r"^T[1-9A-HJ-NP-Za-km-z]{33}$"),
    "TON": re.compile(r"^[A-Za-z0-9_-]{48}$"),
}

_METHOD_CHAIN_OVERRIDE: dict[str, str] = {
    "USDT_TRC20": "TRX",
    "USDT_ERC20": "ETH",
    "USDC_ERC20": "ETH",
    "USDT_BEP20": "ETH",  # BNB Smart Chain is EVM-compatible: same 0x... format
    "USDC_BEP20": "ETH",
    "USDT_SOL": "SOL",
    "USDC_SOL": "SOL",
    "USDT_TON": "TON",
}


def _chain_for(code: str, asset: str) -> str:
    return _METHOD_CHAIN_OVERRIDE.get(code, asset)


def _pattern_for(code: str, asset: str) -> re.Pattern | None:
    return _CHAIN_PATTERNS.get(_chain_for(code, asset))


def _label(method) -> str:
    return f"{method['asset']} · {method['network']}"


# ---------------------------------------------------------------------------
# Browse — "adm:pm" / "adm:pm:v:{code}"
# ---------------------------------------------------------------------------


async def show_methods_root(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    methods = payment_methods_repo.list_all()
    buttons = []
    for m in methods:
        if m["is_active"] and m["wallet_address"]:
            state = "LIVE"
        elif m["wallet_address"]:
            state = "address set, hidden"
        else:
            state = "no address"
        label = f"{_label(m)} — {state}"
        buttons.append([InlineKeyboardButton(label, callback_data=cb("adm", "pm", "v", m["code"]))])
    buttons.append([InlineKeyboardButton("Back to admin", callback_data=cb("adm", "home"))])
    await show_screen(
        update, context, text=bold("Payment methods"), keyboard=InlineKeyboardMarkup(buttons)
    )


async def show_method_detail(
    update: Update, context: ContextTypes.DEFAULT_TYPE, code: str, notice: str | None = None
) -> None:
    method = payment_methods_repo.get(code)
    if method is None:
        await show_methods_root(update, context)
        return

    lines = [bold(_label(method))]
    lines.append(
        f"Address: {render_code(method['wallet_address'])}"
        if method["wallet_address"]
        else "Address: not set"
    )
    if method["memo_tag"]:
        lines.append(f"Memo/tag: {render_code(method['memo_tag'])}")
        lines.append("Customers see this memo/tag as required on the payment screen.")
    lines.append(f"Explorer link: {'configured' if method['explorer_url'] else 'not configured'}")
    if method["is_active"] and method["wallet_address"]:
        status = "LIVE — visible to customers"
    elif method["is_active"]:
        status = "active, but no address — not usable"
    else:
        status = "inactive — hidden from customers"
    lines.append(f"Status: {status}")
    if notice:
        lines += ["", f"✅ {esc(notice)}"]
    text = "\n\n".join(lines)

    addr_label = "Change wallet address" if method["wallet_address"] else "Set wallet address"
    buttons = [[InlineKeyboardButton(addr_label, callback_data=cb("adm", "pm", "addr", code))]]

    if _chain_for(method["code"], method["asset"]) == "TON" or method["memo_tag"]:
        memo_row = [
            InlineKeyboardButton("Set memo tag", callback_data=cb("adm", "pm", "memo", code))
        ]
        if method["memo_tag"]:
            memo_row.append(
                InlineKeyboardButton(
                    "Clear memo tag", callback_data=cb("adm", "pm", "memoclear", code)
                )
            )
        buttons.append(memo_row)

    buttons.append(
        [
            InlineKeyboardButton(
                admin_common.toggle_label(method["is_active"]),
                callback_data=cb("adm", "pm", "tg", code),
            )
        ]
    )
    buttons.append([InlineKeyboardButton("Back to methods", callback_data=cb("adm", "pm"))])

    await show_screen(update, context, text=text, keyboard=InlineKeyboardMarkup(buttons))


# ---------------------------------------------------------------------------
# Setting a wallet address — prompt -> validate -> confirm. Only Confirm writes.
# ---------------------------------------------------------------------------


def _confirm_screen_content(method, address: str) -> tuple[str, InlineKeyboardMarkup]:
    label = bold(_label(method))
    lines = [
        f"Save this as the {label} address? Check it character by character.",
        "",
        render_code(address),
    ]
    if method["wallet_address"]:
        lines += ["", "This replaces the current address. Existing unpaid orders keep the old one."]
    text = "\n".join(lines)
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Confirm", callback_data=cb("adm", "pm", "addrsave", method["code"])
                )
            ],
            [InlineKeyboardButton("Cancel", callback_data=cb("adm", "pm", "v", method["code"]))],
        ]
    )
    return text, keyboard


async def action_addr_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str) -> None:
    method = payment_methods_repo.get(code)
    if method is None:
        await update.callback_query.answer("Not found.", show_alert=True)
        return
    pending_repo.set(update.effective_user.id, "pm_address", target_key=code)
    await admin_common.prompt(
        update, context, f"Paste the wallet address for {bold(_label(method))}."
    )


async def apply_pm_address(update, context, target_key, context_id, raw) -> None:
    code = target_key
    method = payment_methods_repo.get(code)
    if method is None:
        pending_repo.clear(update.effective_user.id)
        await update.message.reply_text("That payment method no longer exists.")
        return

    address = raw.strip()
    if not address or " " in address or len(address) > 200:
        await admin_common.reprompt_after_invalid(
            update, context, "Please paste a single address, up to 200 characters, with no spaces."
        )
        return

    pending_repo.set(
        update.effective_user.id, "pm_address_confirm", target_key=code, pending_value=address
    )

    pattern = _pattern_for(code, method["asset"])
    if pattern is not None and not pattern.match(address):
        text = (
            f"That doesn't look like a {esc(method['network'])} address:\n\n{render_code(address)}"
        )
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "Use it anyway", callback_data=cb("adm", "pm", "addruse", code)
                    )
                ],
                [InlineKeyboardButton("Try again", callback_data=cb("adm", "pm", "addrtry", code))],
                [InlineKeyboardButton("Cancel", callback_data=cb("adm", "pm", "v", code))],
            ]
        )
        await update.message.reply_text(text, reply_markup=keyboard)
        return

    text, keyboard = _confirm_screen_content(method, address)
    await update.message.reply_text(text, reply_markup=keyboard)


async def action_addr_use(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str) -> None:
    pending = pending_repo.get(update.effective_user.id)
    if (
        pending is None
        or pending["action"] != "pm_address_confirm"
        or pending["target_key"] != code
    ):
        await update.callback_query.answer("This request has expired.", show_alert=True)
        return
    method = payment_methods_repo.get(code)
    if method is None:
        await update.callback_query.answer("Not found.", show_alert=True)
        return
    text, keyboard = _confirm_screen_content(method, pending["pending_value"])
    await show_screen(update, context, text=text, keyboard=keyboard)


async def action_addr_try(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str) -> None:
    method = payment_methods_repo.get(code)
    if method is None:
        await update.callback_query.answer("Not found.", show_alert=True)
        return
    pending_repo.set(update.effective_user.id, "pm_address", target_key=code)
    await admin_common.prompt(
        update, context, f"Paste the wallet address for {bold(_label(method))}."
    )


async def action_addr_save(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str) -> None:
    pending = pending_repo.get(update.effective_user.id)
    if (
        pending is None
        or pending["action"] != "pm_address_confirm"
        or pending["target_key"] != code
        or not pending["pending_value"]
    ):
        await update.callback_query.answer(
            "This request has expired — please try again.", show_alert=True
        )
        return

    address = pending["pending_value"]
    payment_methods_repo.set_wallet_address(code, address)
    pending_repo.clear(update.effective_user.id)

    method = payment_methods_repo.get(code)
    lines = [f"Address saved for {bold(_label(method))}."]
    buttons = []
    if not method["is_active"]:
        lines.append(
            "This method is currently inactive — customers won't see it until you activate it."
        )
        buttons.append(
            [InlineKeyboardButton("Activate now", callback_data=cb("adm", "pm", "tg", code))]
        )
    buttons.append(
        [InlineKeyboardButton("Back to method", callback_data=cb("adm", "pm", "v", code))]
    )
    text = "\n\n".join(lines)
    await show_screen(update, context, text=text, keyboard=InlineKeyboardMarkup(buttons))


# ---------------------------------------------------------------------------
# Memo tag
# ---------------------------------------------------------------------------


async def action_memo_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str) -> None:
    method = payment_methods_repo.get(code)
    if method is None:
        await update.callback_query.answer("Not found.", show_alert=True)
        return
    pending_repo.set(update.effective_user.id, "pm_memo", target_key=code)
    await admin_common.prompt(
        update, context, f"Type the memo/tag for {bold(_label(method))} (1–64 characters)."
    )


async def apply_pm_memo(update, context, target_key, context_id, raw) -> None:
    ok, value, message = admin_common.validate_short_text(raw, 1, 64)
    if not ok:
        await admin_common.reprompt_after_invalid(update, context, message)
        return
    payment_methods_repo.set_memo_tag(target_key, value)
    pending_repo.clear(update.effective_user.id)
    await show_method_detail(update, context, target_key, notice="Memo tag saved.")


async def action_memo_clear(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str) -> None:
    method = payment_methods_repo.get(code)
    if method is None:
        await update.callback_query.answer("Not found.", show_alert=True)
        return
    payment_methods_repo.set_memo_tag(code, None)
    await show_method_detail(update, context, code, notice="Memo tag cleared.")


# ---------------------------------------------------------------------------
# Activation
# ---------------------------------------------------------------------------


async def action_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str) -> None:
    method = payment_methods_repo.get(code)
    if method is None:
        await update.callback_query.answer("Not found.", show_alert=True)
        return

    if not method["is_active"]:
        try:
            payment_methods_repo.set_active(code, True)
        except payment_methods_repo.WalletNotSetError:
            await update.callback_query.answer("Set a wallet address first.", show_alert=True)
            return
        await show_method_detail(update, context, code)
        return

    open_count = orders_repo.count_open_for_method(code)
    live_count = payment_methods_repo.count_live()
    lines = [f"Deactivate {bold(_label(method))}?"]
    if open_count:
        noun = "order" if open_count == 1 else "orders"
        lines.append(
            f"{open_count} open {noun} against this method keep working — the address is "
            "snapshotted on each order. The only effect is that new customers can no longer "
            "choose it."
        )
    else:
        lines.append("No open orders are using this method right now.")
    if live_count <= 1:
        lines.append(
            "⚠️ This is your last live payment method — "
            "customers will not be able to check out at all."
        )
    text = "\n\n".join(lines)
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Confirm", callback_data=cb("adm", "pm", "tgc", code)),
                InlineKeyboardButton("Cancel", callback_data=cb("adm", "pm", "v", code)),
            ]
        ]
    )
    await show_screen(update, context, text=text, keyboard=keyboard)


async def action_toggle_confirm(
    update: Update, context: ContextTypes.DEFAULT_TYPE, code: str
) -> None:
    method = payment_methods_repo.get(code)
    if method is None:
        await update.callback_query.answer("Not found.", show_alert=True)
        return
    payment_methods_repo.set_active(code, False)
    await show_method_detail(update, context, code)


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


async def route(update: Update, context: ContextTypes.DEFAULT_TYPE, parts: list[str]) -> None:
    query = update.callback_query
    if len(parts) == 2:
        await show_methods_root(update, context)
        return
    action = parts[2]
    if len(parts) < 4:
        await query.answer("This menu has expired.", show_alert=True)
        return
    code = parts[3]
    if action == "v":
        await show_method_detail(update, context, code)
    elif action == "addr":
        await action_addr_prompt(update, context, code)
    elif action == "addruse":
        await action_addr_use(update, context, code)
    elif action == "addrtry":
        await action_addr_try(update, context, code)
    elif action == "addrsave":
        await action_addr_save(update, context, code)
    elif action == "memo":
        await action_memo_prompt(update, context, code)
    elif action == "memoclear":
        await action_memo_clear(update, context, code)
    elif action == "tg":
        await action_toggle(update, context, code)
    elif action == "tgc":
        await action_toggle_confirm(update, context, code)
    else:
        await query.answer("This menu has expired.", show_alert=True)
