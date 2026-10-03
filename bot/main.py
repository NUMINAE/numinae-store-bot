"""Entrypoint: logging setup, Application construction, handler registration,
and polling. Run as `python -m bot.main` from the project root."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    Defaults,
    MessageHandler,
    TypeHandler,
    filters,
)

from . import jobs, ratelimit
from .config import CONFIG
from .db import init_db
from .handlers import admin_catalogue, admin_orders, catalogue, checkout, router, shipping
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
from .repo import admin_pending as admin_pending_repo


def configure_logging() -> None:
    logging.basicConfig(
        level=CONFIG.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # httpx/httpcore log outgoing request URLs at INFO, and those URLs embed
    # the bot token — must stay above INFO regardless of LOG_LEVEL.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


configure_logging()
logger = logging.getLogger(__name__)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
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
    had_pending = admin_pending_repo.get(CONFIG.owner_id) is not None
    await admin_catalogue.route(update, context)
    if had_pending and admin_pending_repo.get(CONFIG.owner_id) is None:
        lang = get_lang(CONFIG.owner_id)
        await context.bot.send_message(
            chat_id=CONFIG.owner_id,
            text=t("common.menu_restored", lang),
            reply_markup=main_reply_keyboard(lang),
        )


def build_application() -> Application:
    defaults = Defaults(parse_mode=ParseMode.HTML)
    application = Application.builder().token(CONFIG.bot_token).defaults(defaults).build()

    # Group -1 runs before every other handler group, so this applies to
    # both message and callback updates without touching individual
    # handlers. The owner is exempt (see bot/ratelimit.py).
    application.add_handler(TypeHandler(Update, ratelimit.throttle), group=-1)

    application.add_handler(CommandHandler("start", start))
    application.add_handler(
        CommandHandler("admin", admin_catalogue.dashboard, filters=filters.User(CONFIG.owner_id))
    )
    application.add_handler(
        CommandHandler("order", admin_orders.find_order, filters=filters.User(CONFIG.owner_id))
    )
    application.add_handler(
        CommandHandler("sweep", admin_orders.sweep_now, filters=filters.User(CONFIG.owner_id))
    )

    application.add_handler(CallbackQueryHandler(catalogue.route, pattern=r"^cat:"))
    application.add_handler(CallbackQueryHandler(cart_handlers.route, pattern=r"^cart:"))
    application.add_handler(CallbackQueryHandler(checkout.co_route, pattern=r"^co:"))
    application.add_handler(CallbackQueryHandler(checkout.orders_route, pattern=r"^ord:"))
    application.add_handler(CallbackQueryHandler(shipping.route, pattern=r"^sh:"))
    application.add_handler(CallbackQueryHandler(_admin_route, pattern=r"^adm:"))
    application.add_handler(CallbackQueryHandler(menu_show, pattern=r"^menu:"))
    application.add_handler(CallbackQueryHandler(help_placeholder, pattern=r"^help:"))
    application.add_handler(CallbackQueryHandler(lang_route, pattern=r"^lang:"))

    # The single free-text handler must come before the catch-all too.
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, router.route_text))
    # Phase 6: payment proof by screenshot, narrowly scoped to router.route_photo
    # (accepted only with an active order eligible for proof) — photo support
    # was otherwise deliberately removed in Phase 2.5.
    application.add_handler(MessageHandler(filters.PHOTO, router.route_photo))

    # Catch-all must be registered last so it only matches callbacks no
    # earlier handler recognized.
    application.add_handler(CallbackQueryHandler(unknown_callback))

    application.add_error_handler(on_error)

    # A single recurring sweep, not a job per order — see bot/jobs.py for why.
    # A short first-run delay keeps it from firing during startup.
    application.job_queue.run_repeating(  # type: ignore[union-attr]  # job_queue is configured by the [job-queue] extra
        jobs.sweep_job,
        interval=jobs.SWEEP_INTERVAL_SECONDS,
        first=jobs.SWEEP_FIRST_RUN_DELAY_SECONDS,
    )

    return application


def main() -> None:
    logger.info("Initializing database at %s", CONFIG.db_path)
    init_db()

    application = build_application()
    logger.info("Starting bot polling")
    application.run_polling()


if __name__ == "__main__":
    main()
