"""Checkout: cart summary -> asset picker -> network picker -> payment screen
(where the order is actually created) -> hash prompt, plus cancellation and
the "My orders" list/detail screens. Registered under the "co:" and "ord:"
callback namespaces.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import ContextTypes

from ..i18n import status_label, t
from ..keyboards import cb, main_reply_keyboard, parse_callback, parse_cb_id
from ..render import bold, esc, pre, usd
from ..render import code as render_code
from ..repo import cart as cart_repo
from ..repo import customers as customers_repo
from ..repo import orders as orders_repo
from ..repo import payment_methods as payment_methods_repo
from ..repo import products as products_repo
from ..security import get_owned_order
from ..ui import show_screen
from . import router
from .common import get_lang, touch_customer
from .shipping import MAX_ADDRESS_LEN, MIN_ADDRESS_LEN, MIN_ADDRESS_LINES

ORDERS_PAGE_SIZE = 5


# ---------------------------------------------------------------------------
# Checkout entry (from "cart:checkout")
# ---------------------------------------------------------------------------


async def entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    touch_customer(update)
    user_id = update.effective_user.id
    lang = get_lang(user_id)

    lines = cart_repo.list_lines(user_id)
    available = [line for line in lines if products_repo.is_variant_available(line)]

    if not available:
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        t("common.browse_teas_button", lang), callback_data=cb("cat", "cats")
                    )
                ],
                [
                    InlineKeyboardButton(
                        t("common.back_to_menu_button", lang), callback_data=cb("menu", "show")
                    )
                ],
            ]
        )
        await show_screen(update, context, text=t("cart.empty", lang), keyboard=keyboard)
        return

    active_order = orders_repo.get_active_for_user(user_id)
    if active_order is not None:
        back_to_cart = InlineKeyboardButton(
            t("checkout.back_to_cart_button", lang), callback_data=cb("cart", "view")
        )
        await _show_active_order_notice(update, context, active_order, back_to_cart, lang)
        return

    assets = payment_methods_repo.list_active_assets()
    if not assets:
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        t("checkout.back_to_cart_button", lang), callback_data=cb("cart", "view")
                    )
                ]
            ]
        )
        await show_screen(
            update, context, text=t("checkout.payments_unavailable", lang), keyboard=keyboard
        )
        return

    body = [bold(t("checkout.summary_heading", lang)), ""]
    subtotal = 0
    for line in available:
        subtotal += line["line_total"]
        display_name = f"{line['product_name']} {line['variant_label']}".strip()
        body.append(
            t(
                "checkout.line_item",
                lang,
                name=esc(display_name),
                qty=line["quantity"],
                price=usd(line["price_usd"]),
                total=usd(line["line_total"]),
            )
        )
    body += ["", bold(t("checkout.total", lang, amount=usd(subtotal)))]
    text = "\n".join(body)

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t("checkout.continue_button", lang), callback_data=cb("co", "addr")
                )
            ],
            [
                InlineKeyboardButton(
                    t("checkout.back_to_cart_button", lang), callback_data=cb("cart", "view")
                )
            ],
        ]
    )
    await show_screen(update, context, text=text, keyboard=keyboard)


# ---------------------------------------------------------------------------
# Location collection — Phase 6 moved this before payment. Held in
# context.user_data (not written anywhere) until an order actually exists;
# see _create_or_reopen(), which requires it before create_from_cart().
# ---------------------------------------------------------------------------


async def co_addr_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """'Continue' from the order summary, or 'Re-enter' from the confirm
    screen — always starts a fresh location entry."""
    touch_customer(update)
    user_id = update.effective_user.id
    lang = get_lang(user_id)
    customer = customers_repo.get(user_id)
    context.user_data["awaiting_checkout_address"] = True
    context.user_data.pop("pending_checkout_address", None)

    buttons = []
    if customer and customer["last_shipping_text"]:
        buttons.append(
            [
                InlineKeyboardButton(
                    t("checkout.use_previous_location_button", lang),
                    callback_data=cb("co", "addrprev"),
                )
            ]
        )
    buttons.append(
        [
            InlineKeyboardButton(
                t("checkout.back_to_cart_button", lang), callback_data=cb("co", "addrcancel")
            )
        ]
    )
    await show_screen(
        update,
        context,
        text=t("checkout.location_prompt", lang),
        keyboard=InlineKeyboardMarkup(buttons),
    )
    # show_screen above edits the existing message, and ReplyKeyboardRemove
    # can only attach to a new one — a short follow-up carries it instead.
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=t("common.type_below", lang),
        reply_markup=ReplyKeyboardRemove(),
    )


async def co_addr_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """'Back to cart' from the location prompt — the escape hatch before an
    order exists. Restores the reply keyboard on the way out, mirroring
    shipping.cancel_prompt for the legacy post-payment flow."""
    lang = get_lang(update.effective_user.id)
    context.user_data.pop("awaiting_checkout_address", None)
    context.user_data.pop("pending_checkout_address", None)
    from . import cart as cart_handlers

    await cart_handlers.show_cart(update, context)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=t("common.menu_restored", lang),
        reply_markup=main_reply_keyboard(lang),
    )


def _addr_confirmation_content(address: str, lang: str) -> tuple[str, InlineKeyboardMarkup]:
    text = t("checkout.location_confirm_heading", lang) + "\n\n" + pre(address)
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t("checkout.confirm_button", lang), callback_data=cb("co", "addrconfirm")
                )
            ],
            [
                InlineKeyboardButton(
                    t("checkout.reenter_button", lang), callback_data=cb("co", "addr")
                )
            ],
        ]
    )
    return text, keyboard


async def handle_checkout_address_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Called from handlers/router.py when context.user_data has
    awaiting_checkout_address set and the customer sends free text. Same
    bounds as the legacy handle_address_text in shipping.py, imported from
    there rather than redefined, so the two can never drift apart."""
    lang = get_lang(update.effective_user.id)
    raw = update.message.text.strip()
    lines = [line for line in raw.splitlines() if line.strip()]

    if len(raw) > MAX_ADDRESS_LEN:
        await update.message.reply_text(t("checkout.location_too_long", lang, max=MAX_ADDRESS_LEN))
        return

    if len(raw) < MIN_ADDRESS_LEN or len(lines) < MIN_ADDRESS_LINES:
        template = t("checkout.address_template", lang)
        await update.message.reply_text(
            t("checkout.location_too_short", lang, template=pre(template))
        )
        return

    context.user_data["pending_checkout_address"] = raw
    text, keyboard = _addr_confirmation_content(raw, lang)
    await update.message.reply_text(text, reply_markup=keyboard)


async def co_addr_prev(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """'Use my previous location' — one tap straight to the asset picker,
    unlike the legacy flow's confirm step: there's no order yet to confirm
    against, just context.user_data to stage."""
    touch_customer(update)
    user_id = update.effective_user.id
    lang = get_lang(user_id)
    customer = customers_repo.get(user_id)
    if customer is None or not customer["last_shipping_text"]:
        await update.callback_query.answer(
            t("checkout.no_previous_location_alert", lang), show_alert=True
        )
        return
    context.user_data["pending_checkout_address"] = customer["last_shipping_text"]
    context.user_data.pop("awaiting_checkout_address", None)
    await co_asset(update, context)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=t("common.menu_restored", lang),
        reply_markup=main_reply_keyboard(lang),
    )


async def co_addr_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = get_lang(update.effective_user.id)
    pending = context.user_data.get("pending_checkout_address")
    if not pending:
        # Lost to a restart, or reached some other way — send them back to
        # collect it rather than letting anything downstream proceed without one.
        await co_addr_prompt(update, context)
        return
    context.user_data.pop("awaiting_checkout_address", None)
    await co_asset(update, context)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=t("common.menu_restored", lang),
        reply_markup=main_reply_keyboard(lang),
    )


# ---------------------------------------------------------------------------
# Asset / network pickers
# ---------------------------------------------------------------------------


async def co_asset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    touch_customer(update)
    lang = get_lang(update.effective_user.id)
    assets = payment_methods_repo.list_active_assets()
    if not assets:
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        t("checkout.back_to_cart_button", lang), callback_data=cb("cart", "view")
                    )
                ]
            ]
        )
        await show_screen(
            update, context, text=t("checkout.payments_unavailable", lang), keyboard=keyboard
        )
        return

    rows = []
    row: list[InlineKeyboardButton] = []
    for asset in assets:
        row.append(InlineKeyboardButton(asset, callback_data=cb("co", "net", asset)))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(
        [
            InlineKeyboardButton(
                t("checkout.back_to_cart_button", lang), callback_data=cb("cart", "view")
            )
        ]
    )

    await show_screen(
        update, context, text=t("checkout.choose_asset", lang), keyboard=InlineKeyboardMarkup(rows)
    )


async def co_net(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = get_lang(update.effective_user.id)
    parts = parse_callback(update.callback_query.data, 3)
    if parts is None:
        await update.callback_query.answer(t("common.expired_menu", lang), show_alert=True)
        return
    asset = parts[2]
    touch_customer(update)
    methods = payment_methods_repo.list_active_for_asset(asset)

    if not methods:
        await update.callback_query.answer(
            t("checkout.payment_method_unavailable", lang), show_alert=True
        )
        await co_asset(update, context)
        return

    if len(methods) == 1:
        # A single usable network — don't make the customer confirm a choice of one.
        await _create_or_reopen(update, context, methods[0]["code"])
        return

    rows = [
        [InlineKeyboardButton(m["network"], callback_data=cb("co", "pay", m["code"]))]
        for m in methods
    ]
    rows.append(
        [InlineKeyboardButton(t("checkout.back_button", lang), callback_data=cb("co", "asset"))]
    )
    text = t("checkout.choose_network", lang, asset=esc(asset))
    await show_screen(update, context, text=text, keyboard=InlineKeyboardMarkup(rows))


# ---------------------------------------------------------------------------
# Payment screen — this is where the order is created
# ---------------------------------------------------------------------------


async def co_pay(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = get_lang(update.effective_user.id)
    parts = parse_callback(update.callback_query.data, 3)
    if parts is None:
        await update.callback_query.answer(t("common.expired_menu", lang), show_alert=True)
        return
    await _create_or_reopen(update, context, parts[2])


async def _create_or_reopen(
    update: Update, context: ContextTypes.DEFAULT_TYPE, method_code: str
) -> None:
    """The single path to the payment screen: reuses the customer's existing
    active order if there is one, otherwise creates a fresh one from the cart.
    Never creates a second order."""
    touch_customer(update)
    user_id = update.effective_user.id
    lang = get_lang(user_id)

    active = orders_repo.get_active_for_user(user_id)
    dropped_count = 0
    if active is not None:
        if active["status"] != "awaiting_payment":
            back_to_menu = InlineKeyboardButton(
                t("common.back_to_menu_button", lang), callback_data=cb("menu", "show")
            )
            await _show_active_order_notice(update, context, active, back_to_menu, lang)
            return
        order_id = active["id"]
    else:
        location = context.user_data.get("pending_checkout_address")
        if not location:
            # No active order and no staged location — either reached here
            # some other way, or context.user_data was lost to a restart
            # mid-checkout. Send them back to collect it rather than ever
            # creating an order with none: the payment screen must stay
            # unreachable without a location.
            await co_addr_prompt(update, context)
            return
        try:
            order_id, dropped_count = orders_repo.create_from_cart(user_id, method_code, location)
            context.user_data.pop("pending_checkout_address", None)
        except orders_repo.CartEmptyError:
            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            t("common.browse_teas_button", lang), callback_data=cb("cat", "cats")
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            t("common.back_to_menu_button", lang), callback_data=cb("menu", "show")
                        )
                    ],
                ]
            )
            await show_screen(update, context, text=t("cart.empty", lang), keyboard=keyboard)
            return
        except orders_repo.ActiveOrderExistsError as exc:
            existing = orders_repo.get(exc.order_id)
            if existing is not None and existing["status"] != "awaiting_payment":
                back_to_menu = InlineKeyboardButton(
                    t("common.back_to_menu_button", lang), callback_data=cb("menu", "show")
                )
                await _show_active_order_notice(update, context, existing, back_to_menu, lang)
                return
            order_id = exc.order_id
        except orders_repo.PaymentMethodUnavailableError:
            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            t("checkout.choose_payment_method_button", lang),
                            callback_data=cb("co", "asset"),
                        )
                    ]
                ]
            )
            await show_screen(
                update,
                context,
                text=t("checkout.payment_method_unavailable", lang),
                keyboard=keyboard,
            )
            return

    await _show_payment_screen(update, context, order_id, dropped_count)


async def _show_active_order_notice(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    order,
    back_button: InlineKeyboardButton,
    lang: str,
) -> None:
    order_id = order["id"]

    if order["status"] == "rejected":
        body = [t("checkout.active_order_rejected", lang, id=order_id)]
        if order["reject_reason"]:
            body.append(t("checkout.reject_reason_line", lang, reason=esc(order["reject_reason"])))
        text = "\n".join(body)
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        t("checkout.resubmit_hash_button", lang),
                        callback_data=cb("co", "hash", order_id),
                    )
                ],
                [
                    InlineKeyboardButton(
                        t("checkout.cancel_order_id_button", lang, id=order_id),
                        callback_data=cb("co", "cancel", order_id),
                    )
                ],
                [back_button],
            ]
        )
    else:
        status = status_label(order["status"], lang)
        order_heading = bold(t("checkout.order_heading", lang, id=order_id))
        text = t(
            "checkout.already_active_order", lang, order_heading=order_heading, status=esc(status)
        )
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        t("checkout.view_order_button", lang),
                        callback_data=cb("ord", "view", order_id),
                    )
                ],
                [back_button],
            ]
        )

    await show_screen(update, context, text=text, keyboard=keyboard)


async def _show_payment_screen(
    update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: int, dropped_count: int = 0
) -> None:
    lang = get_lang(update.effective_user.id)
    order = orders_repo.get(order_id)
    pm = payment_methods_repo.get(order["payment_method"])

    lines = [bold(t("checkout.order_heading", lang, id=order_id))]
    if dropped_count > 0:
        lines.append(t("checkout.items_removed", lang, count=dropped_count))
    lines += [
        "",
        bold(t("checkout.total", lang, amount=usd(order["total_usd"]))),
        t("checkout.pay_amount", lang, asset=esc(pm["asset"])),
        "",
        bold(t("checkout.network_label", lang, network=esc(pm["network"]))),
        "",
        render_code(order["wallet_address"]),
    ]
    if order["memo_tag"]:
        lines += [
            "",
            render_code(order["memo_tag"]),
            t("checkout.memo_warning", lang),
        ]
    lines += ["", t("checkout.pay_by", lang, expires=esc(order["expires_at"]))]
    lines += ["", t("checkout.proof_note", lang)]
    text = "\n".join(lines)

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t("checkout.hash_button", lang), callback_data=cb("co", "hash", order_id)
                )
            ],
            [
                InlineKeyboardButton(
                    t("checkout.cancel_order_button", lang),
                    callback_data=cb("co", "cancel", order_id),
                )
            ],
            [
                InlineKeyboardButton(
                    t("common.back_to_menu_button", lang), callback_data=cb("menu", "show")
                )
            ],
        ]
    )
    await show_screen(update, context, text=text, keyboard=keyboard)


# ---------------------------------------------------------------------------
# Hash prompt
# ---------------------------------------------------------------------------


async def co_hash(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = get_lang(update.effective_user.id)
    parts = parse_callback(update.callback_query.data, 3)
    order_id = parse_cb_id(parts[2]) if parts else None
    if order_id is None:
        await update.callback_query.answer(t("common.expired_menu", lang), show_alert=True)
        return
    touch_customer(update)

    order = await get_owned_order(update, order_id)
    if order is None:
        return
    if order["status"] not in ("awaiting_payment", "rejected"):
        await update.callback_query.answer(
            t("checkout.hash_no_longer_accept", lang), show_alert=True
        )
        return

    context.user_data["awaiting_hash_order_id"] = order_id

    text = t("checkout.hash_prompt", lang)
    if order["status"] == "awaiting_payment":
        back_button = InlineKeyboardButton(
            t("checkout.back_to_payment_details_button", lang),
            callback_data=cb("co", "pay", order["payment_method"]),
        )
    else:
        back_button = InlineKeyboardButton(
            t("checkout.back_to_order_details_button", lang),
            callback_data=cb("ord", "view", order_id),
        )
    keyboard = InlineKeyboardMarkup(
        [
            [back_button],
            [
                InlineKeyboardButton(
                    t("checkout.cancel_order_button", lang),
                    callback_data=cb("co", "cancel", order_id),
                )
            ],
        ]
    )
    await show_screen(update, context, text=text, keyboard=keyboard)
    # show_screen above edits the existing message, and ReplyKeyboardRemove
    # can only attach to a new one — a short follow-up carries it instead.
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=t("common.type_below", lang),
        reply_markup=ReplyKeyboardRemove(),
    )


async def co_hashok(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = get_lang(update.effective_user.id)
    parts = parse_callback(update.callback_query.data, 3)
    order_id = parse_cb_id(parts[2]) if parts else None
    if order_id is None:
        await update.callback_query.answer(t("common.expired_menu", lang), show_alert=True)
        return

    order = await get_owned_order(update, order_id)
    if order is None:
        return
    pending = context.user_data.get("pending_hash")

    if order["status"] not in ("awaiting_payment", "rejected") or not pending:
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        t("common.back_to_menu_button", lang), callback_data=cb("menu", "show")
                    )
                ]
            ]
        )
        await show_screen(
            update, context, text=t("checkout.request_expired", lang), keyboard=keyboard
        )
        return

    await router.finalize_hash_submission(update, context, order, pending)


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


async def co_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = get_lang(update.effective_user.id)
    parts = parse_callback(update.callback_query.data, (3, 4))
    order_id = parse_cb_id(parts[2]) if parts else None
    if order_id is None:
        await update.callback_query.answer(t("common.expired_menu", lang), show_alert=True)
        return

    # Checked once here rather than in each of the three sub-functions below —
    # all three are only ever reached through this dispatch, and every branch
    # needs the same guarantee that the order belongs to the caller.
    order = await get_owned_order(update, order_id)
    if order is None:
        return

    if len(parts) == 3:
        await _cancel_ask(update, context, order_id)
    else:
        decision = parts[3]
        if decision == "yes":
            await _cancel_confirm(update, context, order_id)
        else:
            await _cancel_abort(update, context, order_id)


async def _cancel_ask(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: int) -> None:
    touch_customer(update)
    lang = get_lang(update.effective_user.id)
    order = orders_repo.get(order_id)
    if order["status"] not in ("awaiting_payment", "rejected"):
        await _cancel_refused(update, context)
        return

    text = t("checkout.cancel_confirm_ask", lang, id=order_id)
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t("checkout.yes_cancel_button", lang),
                    callback_data=cb("co", "cancel", order_id, "yes"),
                ),
                InlineKeyboardButton(
                    t("checkout.keep_it_button", lang),
                    callback_data=cb("co", "cancel", order_id, "no"),
                ),
            ]
        ]
    )
    await show_screen(update, context, text=text, keyboard=keyboard)


async def _cancel_confirm(
    update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: int
) -> None:
    lang = get_lang(update.effective_user.id)
    try:
        orders_repo.cancel(order_id, actor="customer")
    except orders_repo.IllegalTransitionError:
        await _cancel_refused(update, context)
        return

    context.user_data.pop("awaiting_hash_order_id", None)
    context.user_data.pop("pending_hash", None)

    text = t("checkout.order_cancelled", lang, id=order_id)
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t("catalogue.view_cart_button", lang), callback_data=cb("cart", "view")
                )
            ],
            [
                InlineKeyboardButton(
                    t("common.back_to_menu_button", lang), callback_data=cb("menu", "show")
                )
            ],
        ]
    )
    await show_screen(update, context, text=text, keyboard=keyboard)
    # Covers "cancel from the hash prompt" too, since that's this same
    # customer-cancellation completion point regardless of entry screen.
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=t("common.menu_restored", lang),
        reply_markup=main_reply_keyboard(lang),
    )


async def _cancel_abort(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: int) -> None:
    """'No, keep it' — return to whichever screen makes sense for the order's
    current status rather than assuming it's still awaiting payment."""
    lang = get_lang(update.effective_user.id)
    order = orders_repo.get(order_id)
    if order is not None and order["status"] == "rejected":
        back_to_menu = InlineKeyboardButton(
            t("common.back_to_menu_button", lang), callback_data=cb("menu", "show")
        )
        await _show_active_order_notice(update, context, order, back_to_menu, lang)
    else:
        await _show_payment_screen(update, context, order_id)


async def _cancel_refused(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = get_lang(update.effective_user.id)
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t("common.back_to_menu_button", lang), callback_data=cb("menu", "show")
                )
            ]
        ]
    )
    await show_screen(update, context, text=t("checkout.cancel_refused", lang), keyboard=keyboard)


# ---------------------------------------------------------------------------
# "co:" router
# ---------------------------------------------------------------------------


async def co_route(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    lang = get_lang(update.effective_user.id) if update.effective_user else "en"
    parts = parse_callback(query.data, range(2, 5))
    if parts is None:
        await query.answer(t("common.expired_menu", lang), show_alert=True)
        return
    action = parts[1]
    if action == "addr":
        await co_addr_prompt(update, context)
    elif action == "addrprev":
        await co_addr_prev(update, context)
    elif action == "addrconfirm":
        await co_addr_confirm(update, context)
    elif action == "addrcancel":
        await co_addr_cancel(update, context)
    elif action == "asset":
        await co_asset(update, context)
    elif action == "net":
        await co_net(update, context)
    elif action == "pay":
        await co_pay(update, context)
    elif action == "hash":
        await co_hash(update, context)
    elif action == "hashok":
        await co_hashok(update, context)
    elif action == "cancel":
        await co_cancel(update, context)
    else:
        await query.answer(t("common.expired_menu", lang), show_alert=True)


# ---------------------------------------------------------------------------
# "My orders" — list and detail
# ---------------------------------------------------------------------------


async def show_order_list(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int) -> None:
    touch_customer(update)
    user_id = update.effective_user.id
    lang = get_lang(user_id)

    total = orders_repo.count_for_user(user_id)
    if total == 0:
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        t("common.browse_teas_button", lang), callback_data=cb("cat", "cats")
                    )
                ],
                [
                    InlineKeyboardButton(
                        t("common.back_to_menu_button", lang), callback_data=cb("menu", "show")
                    )
                ],
            ]
        )
        await show_screen(
            update, context, text=t("checkout.no_orders_yet", lang), keyboard=keyboard
        )
        return

    total_pages = (total + ORDERS_PAGE_SIZE - 1) // ORDERS_PAGE_SIZE
    page = max(0, min(page, total_pages - 1))
    rows = orders_repo.list_for_user(user_id, ORDERS_PAGE_SIZE, page * ORDERS_PAGE_SIZE)

    buttons = []
    for order in rows:
        status = status_label(order["status"], lang)
        label = t(
            "checkout.order_list_label",
            lang,
            id=order["id"],
            date=order["created_at"][:10],
            total=usd(order["total_usd"]),
            status=status,
        )
        buttons.append([InlineKeyboardButton(label, callback_data=cb("ord", "view", order["id"]))])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀", callback_data=cb("ord", "list", page - 1)))
    nav.append(
        InlineKeyboardButton(
            t("checkout.page_label", lang, page=page + 1, total=total_pages), callback_data="noop"
        )
    )
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("▶", callback_data=cb("ord", "list", page + 1)))
    buttons.append(nav)
    buttons.append(
        [
            InlineKeyboardButton(
                t("common.back_to_menu_button", lang), callback_data=cb("menu", "show")
            )
        ]
    )

    text = bold(t("checkout.your_orders_heading", lang))
    await show_screen(update, context, text=text, keyboard=InlineKeyboardMarkup(buttons))


async def show_order_detail(
    update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: int
) -> None:
    touch_customer(update)
    lang = get_lang(update.effective_user.id)
    order = await get_owned_order(update, order_id)
    if order is None:
        return
    _, items = orders_repo.get_with_items(order_id)

    status = status_label(order["status"], lang)
    body = [bold(t("checkout.order_heading", lang, id=order_id)), f"Status: {esc(status)}", ""]
    for item in items:
        item_name = f"{item['product_name']} {item['variant_label']}".strip()
        body.append(
            t(
                "checkout.line_item",
                lang,
                name=esc(item_name),
                qty=item["quantity"],
                price=usd(item["unit_price_usd"]),
                total=usd(item["line_total_usd"]),
            )
        )
    body += ["", bold(t("checkout.total", lang, amount=usd(order["total_usd"])))]

    buttons = []
    if order["status"] == "awaiting_payment":
        buttons.append(
            [
                InlineKeyboardButton(
                    t("checkout.view_payment_details_button", lang),
                    callback_data=cb("co", "pay", order["payment_method"]),
                )
            ]
        )
    elif order["status"] == "awaiting_verification":
        body.append("")
        if order["tx_hash"]:
            body.append(t("checkout.submitted_hash", lang, hash=render_code(order["tx_hash"])))
        if order["proof_file_id"]:
            body.append(t("checkout.screenshot_submitted", lang))
        body.append(t("checkout.verification_pending", lang))
    elif order["status"] == "rejected":
        if order["reject_reason"]:
            body += ["", t("checkout.reject_reason_line", lang, reason=esc(order["reject_reason"]))]
        buttons.append(
            [
                InlineKeyboardButton(
                    t("checkout.resubmit_hash_button", lang),
                    callback_data=cb("co", "hash", order_id),
                )
            ]
        )
    elif order["status"] == "awaiting_shipping":
        body += ["", t("checkout.payment_confirmed_need_address", lang)]
        buttons.append(
            [
                InlineKeyboardButton(
                    t("checkout.enter_shipping_address_button", lang),
                    callback_data=cb("sh", "prompt", order_id),
                )
            ]
        )
    elif order["status"] == "shipped" and order["tracking_note"]:
        body += ["", t("checkout.tracking_note_line", lang, note=esc(order["tracking_note"]))]

    buttons.append(
        [
            InlineKeyboardButton(
                t("common.back_to_menu_button", lang), callback_data=cb("menu", "show")
            )
        ]
    )
    text = "\n".join(body)
    await show_screen(update, context, text=text, keyboard=InlineKeyboardMarkup(buttons))


async def orders_route(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    lang = get_lang(update.effective_user.id) if update.effective_user else "en"
    parts = parse_callback(query.data, (2, 3))
    if parts is None:
        await query.answer(t("common.expired_menu", lang), show_alert=True)
        return
    action = parts[1]
    if action == "list":
        page = parse_cb_id(parts[2]) if len(parts) > 2 else 0
        if page is None:
            await query.answer(t("common.expired_menu", lang), show_alert=True)
            return
        await show_order_list(update, context, page)
    elif action == "view":
        order_id = parse_cb_id(parts[2]) if len(parts) > 2 else None
        if order_id is None:
            await query.answer(t("common.expired_menu", lang), show_alert=True)
            return
        await show_order_detail(update, context, order_id)
    else:
        await query.answer(t("common.expired_menu", lang), show_alert=True)
