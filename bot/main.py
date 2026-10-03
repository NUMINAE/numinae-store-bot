"""Entrypoint: logging setup, Application construction, handler registration,
and polling. Run as `python -m bot.main` from the project root."""

from __future__ import annotations

import logging
import sys

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    Defaults,
    MessageHandler,
    PreCheckoutQueryHandler,
    TypeHandler,
    filters,
)

from . import jobs, ratelimit
from .config import get_settings
from .db import MigrationError, init_db
from .handlers import admin_catalogue, catalogue, checkout, library, payments, router
from .handlers import cart as cart_handlers
from .handlers.common import (
    get_lang,
    help_placeholder,
    lang_route,
    menu_show,
    start,
    unknown_callback,
)
from .i18n import t
from .keyboards import main_reply_keyboard
from .payments.registry import build_provider
from .repo import admin_pending as admin_pending_repo
from .seed_demo import seed_demo
from .tg import IgnoreUpdate


def configure_logging() -> None:
    logging.basicConfig(
        level=get_settings().log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # httpx/httpcore log outgoing request URLs at INFO, and those URLs embed
    # the bot token — must stay above INFO regardless of LOG_LEVEL.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


logger = logging.getLogger(__name__)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    if isinstance(context.error, IgnoreUpdate):
        logger.debug("Ignored update: %s", context.error)
        return
    user_id = None
    action = None
    if isinstance(update, Update):
        if update.effective_user:
            user_id = update.effective_user.id
        if update.callback_query:
            action = f"callback_data={update.callback_query.data!r}"
        elif update.message and update.message.text:
            action = f"message_text={update.message.text!r}"
    logger.exception(
        "Unhandled exception (user_id=%s, %s)", user_id, action, exc_info=context.error
    )


async def _admin_route(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Wraps admin_catalogue.route so the reply keyboard is restored when a
    typed-input prompt is cancelled via its inline Cancel button — the only
    way a callback (rather than typed text) clears a pending admin-input
    row. Kept here, not in admin_catalogue.py, so that module stays
    untouched beyond the prompt-side removal already in
    handlers/admin_common.py's prompt()."""
    owner_id = get_settings().owner_id
    had_pending = admin_pending_repo.get(owner_id) is not None
    await admin_catalogue.route(update, context)
    if had_pending and admin_pending_repo.get(owner_id) is None:
        lang = get_lang(owner_id)
        await context.bot.send_message(
            chat_id=owner_id,
            text=t("common.menu_restored", lang),
            reply_markup=main_reply_keyboard(lang),
        )


def build_application() -> Application:
    defaults = Defaults(parse_mode=ParseMode.HTML)
    settings = get_settings()
    application = (
        Application.builder()
        .token(settings.bot_token.get_secret_value())
        .defaults(defaults)
        .build()
    )

    # Group -1 runs before every other handler group, so this applies to
    # both message and callback updates without touching individual
    # handlers. The owner is exempt (see bot/ratelimit.py).
    # Payment updates (pre_checkout_query, successful_payment) are exempt
    # inside the throttle itself.
    application.add_handler(TypeHandler(Update, ratelimit.throttle), group=-1)

    application.bot_data[payments.PROVIDER_KEY] = build_provider(settings.payment_mode)

    application.add_handler(CommandHandler("start", start))
    application.add_handler(
        CommandHandler("admin", admin_catalogue.dashboard, filters=filters.User(settings.owner_id))
    )
    application.add_handler(CommandHandler("paysupport", payments.paysupport))
    # /sweep is owner-only through its decorator, so a stranger is told so
    # rather than silently ignored.
    application.add_handler(CommandHandler("sweep", jobs.sweep_command))

    # Payments: both must be registered before the catch-all handlers below.
    application.add_handler(PreCheckoutQueryHandler(payments.pre_checkout))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, payments.successful_payment))

    application.add_handler(CallbackQueryHandler(catalogue.route, pattern=r"^cat:"))
    application.add_handler(CallbackQueryHandler(cart_handlers.route, pattern=r"^cart:"))
    application.add_handler(CallbackQueryHandler(_admin_route, pattern=r"^adm:"))
    application.add_handler(CallbackQueryHandler(checkout.route, pattern=r"^co:"))
    application.add_handler(CallbackQueryHandler(library.route, pattern=r"^lib:"))
    application.add_handler(CallbackQueryHandler(menu_show, pattern=r"^menu:"))
    application.add_handler(CallbackQueryHandler(help_placeholder, pattern=r"^help:"))
    application.add_handler(CallbackQueryHandler(lang_route, pattern=r"^lang:"))

    # The single free-text handler must come before the catch-all too.
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, router.route_text))

    # Catch-all must be registered last so it only matches callbacks no
    # earlier handler recognized.
    application.add_handler(CallbackQueryHandler(unknown_callback))

    application.add_error_handler(on_error)

    # One recurring sweep (not a job per order); a short first-run delay keeps
    # it from firing during startup.
    if application.job_queue is not None:
        application.job_queue.run_repeating(
            jobs.sweep_job,
            interval=jobs.SWEEP_INTERVAL_SECONDS,
            first=jobs.SWEEP_FIRST_RUN_DELAY_SECONDS,
        )

    return application


def main() -> None:
    settings = get_settings()
    configure_logging()
    logger.info("Initializing database at %s", settings.db_path)
    try:
        init_db()
    except MigrationError as exc:
        sys.exit(f"Database migration failed: {exc}")
    if seed_demo():
        logger.info("Demo catalogue loaded (SEED_DEMO is on)")

    application = build_application()
    logger.info("Starting bot polling")
    application.run_polling()


if __name__ == "__main__":
    main()
