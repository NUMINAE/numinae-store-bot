from __future__ import annotations

import logging

import pytest

from bot import owner_notify
from bot.repo import settings as settings_repo

from .conftest import OWNER_ID
from .helpers import add_customer, make_context, make_user


@pytest.fixture(autouse=True)
def reset_window():
    def reset():
        owner_notify._window_open = False
        owner_notify._sent_count = 0
        owner_notify._suppressed_count = 0

    reset()
    yield
    reset()


async def _notify(context, n: int = 1, start: int = 3000):
    for i in range(n):
        uid = add_customer(start + i)
        await owner_notify.notify_new_customer(context, make_user(uid, "Ann", "ann"))


async def test_first_customer_is_sent_to_the_owner_and_opens_a_window(db):
    context = make_context()
    await _notify(context)
    context.bot.send_message.assert_awaited_once()
    chat_id, text = context.bot.send_message.await_args.args
    assert chat_id == OWNER_ID
    # No explicit parse_mode: relies on the Application's Defaults(parse_mode=HTML).
    assert "<b>New customer</b>" in text
    assert "Ann" in text
    assert "@ann" in text
    assert "<code>3000</code>" in text
    assert "customer #1" in text
    context.job_queue.run_once.assert_called_once()
    args = context.job_queue.run_once.call_args
    assert args.args[1] == owner_notify.WINDOW_SECONDS


async def test_limit_per_window_then_suppressed_and_counted(db):
    context = make_context()
    await _notify(context, owner_notify.LIMIT_PER_WINDOW + 3)
    assert context.bot.send_message.await_count == owner_notify.LIMIT_PER_WINDOW
    assert owner_notify._suppressed_count == 3
    context.job_queue.run_once.assert_called_once()  # one rollup job per window


async def test_rollup_reports_suppressed_and_resets_the_window(db):
    context = make_context()
    await _notify(context, owner_notify.LIMIT_PER_WINDOW + 2)
    context.bot.send_message.reset_mock()

    await owner_notify._rollup_job(context)

    context.bot.send_message.assert_awaited_once_with(
        OWNER_ID, "2 more new customers in the last hour"
    )
    assert owner_notify._window_open is False
    assert (owner_notify._sent_count, owner_notify._suppressed_count) == (0, 0)

    # The next signup opens a fresh window and a fresh rollup job.
    context.bot.send_message.reset_mock()
    await _notify(context, start=4000)
    context.bot.send_message.assert_awaited_once()
    assert context.job_queue.run_once.call_count == 2


async def test_rollup_uses_the_singular_for_one(db):
    context = make_context()
    await _notify(context, owner_notify.LIMIT_PER_WINDOW + 1)
    context.bot.send_message.reset_mock()
    await owner_notify._rollup_job(context)
    context.bot.send_message.assert_awaited_once_with(
        OWNER_ID, "1 more new customer in the last hour"
    )


async def test_rollup_with_nothing_suppressed_sends_nothing(db):
    context = make_context()
    await _notify(context, 2)
    context.bot.send_message.reset_mock()
    await owner_notify._rollup_job(context)
    context.bot.send_message.assert_not_called()
    assert owner_notify._window_open is False


async def test_disabled_setting_silences_individual_notifications(db):
    settings_repo.set("notify_new_customers", "0")
    context = make_context()
    await _notify(context, 2)
    context.bot.send_message.assert_not_called()
    context.job_queue.run_once.assert_not_called()


async def test_disabled_setting_silences_the_rollup_but_still_resets(db):
    context = make_context()
    await _notify(context, owner_notify.LIMIT_PER_WINDOW + 1)
    context.bot.send_message.reset_mock()
    settings_repo.set("notify_new_customers", "0")
    await owner_notify._rollup_job(context)
    context.bot.send_message.assert_not_called()
    assert owner_notify._window_open is False


async def test_missing_setting_defaults_to_enabled(db):
    db.execute("DELETE FROM settings WHERE key = 'notify_new_customers'")
    db.commit()
    context = make_context()
    await _notify(context)
    context.bot.send_message.assert_awaited_once()


async def test_user_supplied_fields_are_not_trusted_as_markup(db):
    context = make_context()
    add_customer(3000)
    user = make_user(3000, "<b>&", "<u>x")
    await owner_notify.notify_new_customer(context, user)
    text = context.bot.send_message.await_args.args[1]
    assert "&lt;b&gt;&amp;" in text
    assert "<u>" not in text.replace("&lt;u&gt;", "")


async def test_user_without_a_first_name_or_username(db):
    context = make_context()
    add_customer(3000)
    await owner_notify.notify_new_customer(context, make_user(3000, None, None))
    text = context.bot.send_message.await_args.args[1]
    assert "(no name)" in text
    assert "@" not in text


async def test_send_failure_is_logged_not_raised(db, caplog):
    caplog.set_level(logging.ERROR)
    context = make_context()
    context.bot.send_message.side_effect = RuntimeError("telegram down")
    await _notify(context)
    assert any("Failed to send new-customer notification" in r.getMessage() for r in caplog.records)


async def test_rollup_send_failure_is_logged_not_raised(db, caplog):
    caplog.set_level(logging.ERROR)
    context = make_context()
    await _notify(context, owner_notify.LIMIT_PER_WINDOW + 1)
    context.bot.send_message.side_effect = RuntimeError("telegram down")
    await owner_notify._rollup_job(context)
    assert any("Failed to send new-customer rollup" in r.getMessage() for r in caplog.records)
