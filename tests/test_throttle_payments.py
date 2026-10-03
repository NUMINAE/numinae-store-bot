"""Payment updates are never throttled: a dropped pre_checkout_query cancels
the customer's payment, and a dropped successful_payment means they paid and
the bot never recorded it. Ordinary messages from the same user stay throttled."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from telegram.ext import ApplicationHandlerStop

from bot import ratelimit
from bot.handlers import payments
from bot.repo import orders as orders_repo

from .helpers import make_callback_query, make_message, make_update
from .shop import TEXT, place_order
from .test_payment_handlers import (
    payment_update,
    pre_checkout_query,
    stars_context,
    texts_to,
)

USER = 3001


@pytest.fixture
def clock(monkeypatch, clean_ratelimit):
    now = [1000.0]
    monkeypatch.setattr(ratelimit, "time", SimpleNamespace(monotonic=lambda: now[0]))
    return now


async def exhaust(user_id: int) -> None:
    for _ in range(ratelimit.LIMIT):
        await ratelimit.throttle(
            make_update(user_id, callback_query=make_callback_query()), stars_context()
        )
    with pytest.raises(ApplicationHandlerStop):  # the limit is genuinely exhausted
        await ratelimit.throttle(
            make_update(user_id, callback_query=make_callback_query()), stars_context()
        )


async def test_a_pre_checkout_query_is_answered_even_when_the_user_is_throttled(db, clock):
    order_id = place_order(USER, {TEXT: 1})
    await exhaust(USER)
    context = stars_context()
    query = pre_checkout_query(USER, str(order_id), 75)
    update = make_update(USER, pre_checkout_query=query)

    await ratelimit.throttle(update, context)  # must not raise ApplicationHandlerStop
    await payments.pre_checkout(update, context)

    query.answer.assert_awaited_once_with(ok=True)


async def test_a_successful_payment_is_processed_even_when_the_user_is_throttled(db, clock):
    order_id = place_order(USER, {TEXT: 1})
    await exhaust(USER)
    context = stars_context()
    update = payment_update(USER, order_id, 75, "ch_throttled")

    await ratelimit.throttle(update, context)  # must not raise ApplicationHandlerStop
    await payments.successful_payment(update, context)
    while context.tasks:
        await context.tasks.pop()

    order = orders_repo.get(order_id)
    assert (order["status"], order["telegram_payment_charge_id"]) == ("delivered", "ch_throttled")
    assert any("Payment received" in m for m in texts_to(context, USER))
    update.message.reply_text.assert_not_called()  # and no "going too fast" reply


async def test_payment_updates_do_not_count_toward_the_limit(db, clock):
    for _ in range(ratelimit.LIMIT * 3):
        await ratelimit.throttle(
            make_update(USER, pre_checkout_query=pre_checkout_query(USER, "1", 1)), stars_context()
        )
        await ratelimit.throttle(payment_update(USER, 1, 1, "c"), stars_context())
    # The user's own budget is untouched: 20 ordinary taps still pass.
    for _ in range(ratelimit.LIMIT):
        await ratelimit.throttle(
            make_update(USER, callback_query=make_callback_query()), stars_context()
        )


async def test_ordinary_messages_from_a_throttled_user_are_still_blocked(db, clock):
    await exhaust(USER)
    message = make_message("hello")
    with pytest.raises(ApplicationHandlerStop):
        await ratelimit.throttle(make_update(USER, message=message), stars_context())
    message.reply_text.assert_awaited_once()


async def test_a_plain_message_is_not_mistaken_for_a_payment(db, clock):
    await exhaust(USER)
    message = make_message("successful_payment")  # successful_payment attribute stays None
    with pytest.raises(ApplicationHandlerStop):
        await ratelimit.throttle(make_update(USER, message=message), stars_context())


def test_the_throttle_runs_in_a_group_before_the_payment_handlers(monkeypatch, tmp_path):
    """The exemption is inside the throttle itself, and the throttle is
    registered ahead of everything (group -1) while the payment handlers sit
    before the catch-all, so the ordering cannot drop a payment update."""
    from telegram.ext import CallbackQueryHandler, MessageHandler, PreCheckoutQueryHandler

    from bot import db as db_module
    from bot import main
    from bot.config import get_settings

    db_module.close_conn()
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    get_settings.cache_clear()
    application = main.build_application()

    assert min(application.handlers) == -1
    throttle = application.handlers[-1][0]
    assert throttle.callback is ratelimit.throttle

    group0 = application.handlers[0]
    kinds = [type(h) for h in group0]
    pre_checkout_at = kinds.index(PreCheckoutQueryHandler)
    payment_at = next(
        i
        for i, h in enumerate(group0)
        if isinstance(h, MessageHandler) and h.callback is payments.successful_payment
    )
    catch_all_at = next(
        i for i, h in enumerate(group0) if isinstance(h, CallbackQueryHandler) and h.pattern is None
    )
    assert pre_checkout_at < catch_all_at and payment_at < catch_all_at
    db_module.close_conn()
    get_settings.cache_clear()
