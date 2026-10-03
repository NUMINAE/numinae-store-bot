"""Payment handlers: the pre-checkout answer, the successful-payment recorder,
the shared completion path used by the demo "Pay" button too, and /paysupport.

Rules this module lives by:
- pre_checkout_query must be answered within 10 seconds, so the handler does
  only local work (no network call besides the answer itself) and always
  answers, even on an unexpected error.
- Goods are delivered only after a recorded successful payment. Answering
  pre-checkout means nothing.
- Every send to a customer or the owner is failure-isolated: a blocked chat
  never prevents the payment from being recorded.
"""

from __future__ import annotations

import logging
from typing import Any

from telegram import Bot, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from .. import delivery
from ..config import get_settings
from ..i18n import DEFAULT_LANGUAGE, t
from ..payments.base import PaymentProvider
from ..payments.checks import REJECTION_MESSAGE_KEY, PayRejection, check_payable
from ..render import bold, esc, stars
from ..repo import customers as customers_repo
from ..repo import orders as orders_repo
from ..repo import settings as settings_repo
from ..tg import require_message, require_user
from .common import get_lang, touch_customer

logger = logging.getLogger(__name__)

PROVIDER_KEY = "payment_provider"
RESERVATION_EXTENSION_MINUTES = 2


def get_provider(context: ContextTypes.DEFAULT_TYPE) -> PaymentProvider:
    provider: PaymentProvider = context.bot_data[PROVIDER_KEY]
    return provider


def parse_order_id(payload: str) -> int | None:
    """The invoice payload is the order id and nothing else."""
    try:
        order_id = int(payload)
    except (TypeError, ValueError):
        return None
    return order_id if 0 < order_id <= 2**63 - 1 else None


def _lang_for(user_id: int) -> str:
    customer = customers_repo.get(user_id)
    return customer["language"] if customer else DEFAULT_LANGUAGE


# ---------------------------------------------------------------------------
# Isolated sends
# ---------------------------------------------------------------------------


async def safe_send(bot: Bot, chat_id: int, text: str, **kwargs: Any) -> bool:
    """Send a message, swallowing and logging any failure."""
    try:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML, **kwargs)
    except Exception:
        logger.error("Failed to send a message (chat_id=%s)", chat_id, exc_info=True)
        return False
    return True


async def alert_owner(bot: Bot, text: str) -> None:
    """Owner alerts are English. Isolated like every other send."""
    await safe_send(bot, get_settings().owner_id, text)


def _buyer(user_id: int) -> str:
    customer = customers_repo.get(user_id)
    if customer is not None and customer["username"]:
        return f"@{esc(customer['username'])} (id {user_id})"
    return f"id {user_id}"


def _items_text(order_id: int) -> str:
    _, items = orders_repo.get_with_items(order_id)
    return ", ".join(
        esc(f"{i['product_name']} — {i['variant_label']}") + f" × {i['quantity']}" for i in items
    )


# ---------------------------------------------------------------------------
# Pre-checkout
# ---------------------------------------------------------------------------


async def pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.pre_checkout_query
    if query is None:
        return
    try:
        provider = get_provider(context)
        order_id = parse_order_id(query.invoice_payload)
        order = orders_repo.get(order_id) if order_id is not None else None
        rejection = check_payable(
            order, query.from_user.id, query.total_amount, query.currency, provider.mode
        )
        if rejection is None and order_id is not None:
            # Hold the keys long enough for the payment to land.
            orders_repo.extend_reservation(order_id, RESERVATION_EXTENSION_MINUTES)
            await query.answer(ok=True)
            return
        rejection = rejection or PayRejection.NOT_FOUND
        logger.info(
            "Pre-checkout refused (order_id=%s, user_id=%s, reason=%s)",
            order_id,
            query.from_user.id,
            rejection.value,
        )
        lang = _lang_for(query.from_user.id)
        await query.answer(ok=False, error_message=t(REJECTION_MESSAGE_KEY[rejection], lang))
    except Exception:
        logger.error("Pre-checkout handler failed", exc_info=True)
        try:
            await query.answer(
                ok=False,
                error_message=t(
                    REJECTION_MESSAGE_KEY[PayRejection.NOT_FOUND], _lang_for(query.from_user.id)
                ),
            )
        except Exception:
            logger.error("Could not answer the pre-checkout query", exc_info=True)


# ---------------------------------------------------------------------------
# Recording a payment (real or demo)
# ---------------------------------------------------------------------------


async def _deliver_in_background(bot: Bot, order_id: int) -> None:
    try:
        result = await delivery.deliver_order(bot, order_id)
        if result.failed:
            logger.error(
                "Delivery incomplete for order %s (failed items: %s)", order_id, result.failed
            )
    except Exception:
        logger.error("Delivery crashed for order %s", order_id, exc_info=True)


async def process_payment(
    context: ContextTypes.DEFAULT_TYPE,
    order_id: int,
    payer_id: int,
    charge_id: str,
    amount: int,
    currency: str,
) -> orders_repo.PaymentResult:
    """Record a payment and act on the outcome. The single completion path:
    Stars payments and the demo button both end up here."""
    bot = context.bot
    provider = get_provider(context)
    lang = _lang_for(payer_id)
    result = orders_repo.complete_payment(order_id, payer_id, charge_id, amount, currency)
    outcome = result.outcome

    if outcome is orders_repo.PaymentOutcome.PAID:
        key = "pay.received_delayed" if result.short_keys else "pay.received"
        await safe_send(bot, payer_id, t(key, lang, id=order_id))
        order = orders_repo.get(order_id)
        total = stars(order["total_stars"]) if order is not None else "?"
        await alert_owner(
            bot,
            f"{bold(f'New paid order #{order_id}')}\n"
            f"Buyer: {_buyer(payer_id)}\nItems: {_items_text(order_id)}\nTotal: {total}",
        )
        if result.short_keys:
            sold, needed = result.short_keys
            await alert_owner(
                bot,
                f"⚠️ {bold(f'Order #{order_id}: payment received but keys are short')}\n"
                f"Only {sold} of {needed} reserved keys were available at payment time.\n"
                f"Buyer id: {payer_id}\nItems: {_items_text(order_id)}\n"
                f"Charge id: {esc(charge_id)}\nRefund or add keys.",
            )
        # Delivery runs in the background so this handler returns promptly.
        context.application.create_task(_deliver_in_background(bot, order_id))
    elif outcome is orders_repo.PaymentOutcome.DUPLICATE:
        logger.info("Duplicate payment ignored (order_id=%s, charge_id=%s)", order_id, charge_id)
    elif outcome is orders_repo.PaymentOutcome.ORPHAN:
        await _handle_orphan(context, provider, order_id, payer_id, charge_id, lang, result)
    else:  # MISMATCH
        logger.error(
            "Payment mismatch (order_id=%s, user_id=%s, charge_id=%s, amount=%s %s)",
            order_id,
            payer_id,
            charge_id,
            amount,
            currency,
        )
        await safe_send(bot, payer_id, t("pay.mismatch", lang, id=order_id))
        await alert_owner(
            bot,
            f"🚨 {bold('Payment could not be matched')}\nOrder: #{order_id}\n"
            f"Buyer id: {payer_id}\nCharge id: {esc(charge_id)}\n"
            f"Amount: {amount} {esc(currency)}\nNothing was delivered.",
        )
    return result


async def _handle_orphan(
    context: ContextTypes.DEFAULT_TYPE,
    provider: PaymentProvider,
    order_id: int,
    payer_id: int,
    charge_id: str,
    lang: str,
    result: orders_repo.PaymentResult,
) -> None:
    """The order can't take this payment: refund THIS charge (the one in the
    incoming update, never the order's stored one) and tell everyone."""
    bot = context.bot
    expired = result.reason is orders_repo.OrphanReason.EXPIRED_OR_CANCELLED
    refunded = False
    try:
        refunded = bool(await provider.refund(bot, payer_id, charge_id))
    except Exception:
        logger.error(
            "Refund raised (order_id=%s, charge_id=%s)", order_id, charge_id, exc_info=True
        )
    if not refunded:
        logger.error("Refund failed (order_id=%s, charge_id=%s)", order_id, charge_id)

    why = "order expired or cancelled" if expired else "duplicate charge on an already paid order"
    if refunded:
        customer_key = "pay.orphan_expired" if expired else "pay.orphan_paid"
        await safe_send(bot, payer_id, t(customer_key, lang, id=order_id))
        await alert_owner(
            bot,
            f"ℹ️ Payment refunded automatically ({why}).\nOrder: #{order_id}\n"
            f"Buyer id: {payer_id}\nCharge id: {esc(charge_id)}",
        )
    else:
        await safe_send(bot, payer_id, t("pay.orphan_refund_failed", lang, id=order_id))
        await alert_owner(
            bot,
            f"🚨 {bold('REFUND FAILED — manual action needed')} ({why}).\nOrder: #{order_id}\n"
            f"Buyer id: {payer_id}\nCharge id: {esc(charge_id)}",
        )


async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = require_message(update)
    payment = message.successful_payment
    if payment is None:
        return
    user = require_user(update)
    order_id = parse_order_id(payment.invoice_payload)
    # Receipt first, before anything can fail. Ids only: never the update object.
    logger.info(
        "Payment receipt: order_id=%s user_id=%s charge_id=%s amount=%s %s",
        order_id,
        user.id,
        payment.telegram_payment_charge_id,
        payment.total_amount,
        payment.currency,
    )
    if order_id is None:
        logger.error("Payment with an unreadable payload (user_id=%s)", user.id)
        await alert_owner(
            context.bot,
            f"🚨 {bold('Payment with an unreadable payload')}\nBuyer id: {user.id}\n"
            f"Charge id: {esc(payment.telegram_payment_charge_id)}\n"
            f"Amount: {payment.total_amount} {esc(payment.currency)}",
        )
        return
    await process_payment(
        context,
        order_id,
        user.id,
        payment.telegram_payment_charge_id,
        payment.total_amount,
        payment.currency,
    )


# ---------------------------------------------------------------------------
# /paysupport
# ---------------------------------------------------------------------------


async def paysupport(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = require_message(update)
    touch_customer(update)
    lang = get_lang(require_user(update).id)
    note = settings_repo.get("support_note", "") or ""
    await message.reply_text(t("pay.support_text", lang, note=esc(note)))
