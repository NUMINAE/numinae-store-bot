"""Entrypoint: logging setup, Application construction, handler registration,
and polling. Run as `python -m bot.main` from the project root."""

from __future__ import annotations

import logging
import sys
import traceback
from types import TracebackType

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

from . import jobs, profile, ratelimit
from .concurrency import PerUserUpdateProcessor
from .config import get_settings
from .db import MigrationError, init_db
from .handlers import (
    admin_catalogue,
    admin_orders,
    admin_uploads,
    catalogue,
    checkout,
    library,
    payments,
    router,
)
from .handlers import cart as cart_handlers
from .handlers.common import (
    help_placeholder,
    menu_show,
    start,
    unknown_callback,
)
from .i18n import t
from .keyboards import main_reply_keyboard
from .payments.registry import build_provider
from .redact import RedactingFormatter, redact
from .repo import admin_pending as admin_pending_repo
from .seed_demo import seed_demo
from .tg import IgnoreUpdate

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging() -> None:
    """Root logging with every handler formatted through RedactingFormatter, so
    a token that slips into a log message, its args or a traceback is redacted
    in the final output."""
    root = logging.getLogger()
    if not root.handlers:
        root.addHandler(logging.StreamHandler())
    formatter = RedactingFormatter(LOG_FORMAT)
    for handler in root.handlers:
        handler.setFormatter(formatter)
    level: int = getattr(logging, get_settings().log_level)
    root.setLevel(level)
    # Whatever LOG_LEVEL says, these stay quiet:
    # - httpx/httpcore log outgoing request URLs at INFO, and those URLs embed
    #   the bot token, so they never go below WARNING;
    # - telegram logs whole Update objects at DEBUG (message text, pasted
    #   licence keys, names), so it never goes below INFO.
    logging.getLogger("httpx").setLevel(max(level, logging.WARNING))
    logging.getLogger("httpcore").setLevel(max(level, logging.WARNING))
    logging.getLogger("telegram").setLevel(max(level, logging.INFO))


logger = logging.getLogger(__name__)


MAX_LOGGED_CALLBACK_BYTES = 64  # Telegram's own callback_data limit

_UPDATE_KINDS = ("callback_query", "pre_checkout_query", "message", "edited_message")


def _update_kind(update: object) -> str:
    if not isinstance(update, Update):
        return type(update).__name__
    for kind in _UPDATE_KINDS:
        if getattr(update, kind, None) is not None:
            return kind
    return "other"


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log an unhandled handler error WITHOUT user content: only the exception
    class and (redacted) message, the update kind, the user id and, for button
    taps, the callback_data (a bot-built string of at most 64 bytes). Never
    message text, document names or anything else a user or the owner typed."""
    error = context.error
    if isinstance(error, IgnoreUpdate):
        logger.debug("Ignored update: %s", error)
        return
    user_id = None
    callback_data = None
    if isinstance(update, Update):
        if update.effective_user:
            user_id = update.effective_user.id
        if update.callback_query and update.callback_query.data:
            callback_data = update.callback_query.data.encode("utf-8")[
                :MAX_LOGGED_CALLBACK_BYTES
            ].decode("utf-8", "ignore")
    logger.error(
        "Unhandled %s: %s (update=%s, user_id=%s, callback_data=%r)",
        type(error).__name__,
        redact(str(error)),
        _update_kind(update),
        user_id,
        callback_data,
        exc_info=error,
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
        await context.bot.send_message(
            chat_id=owner_id,
            text=t("common.menu_restored"),
            reply_markup=main_reply_keyboard(),
        )


async def _post_init(application: Application) -> None:
    """Sync the bot's Telegram profile once at startup. Best-effort: nothing
    here can stop the bot from starting."""
    try:
        await profile.sync_profile(application.bot, get_settings().owner_id)
    except Exception as exc:
        logger.warning("Profile sync failed: %s", redact(str(exc)))


def build_application() -> Application:
    defaults = Defaults(parse_mode=ParseMode.HTML)
    settings = get_settings()
    application = (
        Application.builder()
        .token(settings.bot_token.get_secret_value())
        .defaults(defaults)
        .post_init(_post_init)
        .concurrent_updates(PerUserUpdateProcessor())
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
    application.add_handler(CommandHandler("order", admin_orders.find_order))
    application.add_handler(CommandHandler("library", library.library_command))
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

    # The single free-text handler must come before the catch-all too.
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, router.route_text))
    # Owner uploads (delivery files, licence-key .txt files). Documents from anyone
    # else, or without a pending prompt, only get a nudge: nothing is stored or
    # downloaded. Other media is only acted on while a file/keys prompt is open.
    application.add_handler(MessageHandler(filters.Document.ALL, admin_uploads.on_document))
    application.add_handler(
        MessageHandler(
            filters.PHOTO | filters.VIDEO | filters.AUDIO | filters.VOICE | filters.Sticker.ALL,
            admin_uploads.on_other_media,
        )
    )

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


def _redacting_excepthook(
    exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None
) -> None:
    """Print an uncaught exception to stderr with anything token-shaped redacted.
    The interpreter's default hook bypasses the logging formatter, and some
    library exceptions (an invalid token, a failed request) quote the token."""
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    sys.stderr.write(redact(text))


def install_excepthook() -> None:
    sys.excepthook = _redacting_excepthook


def _log_critical_redacted(exc: BaseException) -> None:
    """Log a fatal startup/polling error through the (redacted) logging system.
    The traceback is formatted here and redacted before it is logged, so no
    handler (not even one without the redacting formatter) ever sees a token."""
    if not logging.getLogger().handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(RedactingFormatter(LOG_FORMAT))
        logging.getLogger().addHandler(handler)
    detail = redact("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    logger.critical("Fatal error, shutting down:\n%s", detail)


def main() -> None:
    install_excepthook()  # first of all: before config is even read
    try:
        _run()
    except Exception as exc:
        _log_critical_redacted(exc)
        raise SystemExit(1) from None


def _run() -> None:
    settings = get_settings()
    configure_logging()
    if settings.payment_mode == "mock":
        logger.warning(
            "DEMO MODE: Pay (demo) grants real content without payment. "
            "Never run this mode with real content."
        )
    else:
        logger.info("Payment mode: %s", settings.payment_mode)
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
