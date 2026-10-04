# Numinae Store

A Telegram bot storefront for digital goods — files, licence keys and guides for creators and developers — paid with Telegram Stars.

**Status: work in progress.** Browsing, the cart, checkout (Telegram Stars or a demo mode), delivery, the customer library and the admin tools (uploads, keys, orders, refunds, stats) work.

## Requirements

Python 3.11 or newer, and **SQLite 3.35 or newer** — migration 0004 uses `ALTER TABLE ... DROP COLUMN`, which older SQLite versions lack. Python's bundled SQLite is normally newer; check with `python -c "import sqlite3; print(sqlite3.sqlite_version)"`. The bot is English-only.

## Setup (Windows / VS Code)

1. Activate the virtual environment:

   ```powershell
   .\.venv\Scripts\Activate.ps1
   ```

2. Install dependencies:

   ```powershell
   pip install -e ".[dev]"
   ```

3. Copy `.env.example` to `.env` and fill in the values:

   ```powershell
   Copy-Item .env.example .env
   ```

   - **BOT_TOKEN**: message [@BotFather](https://t.me/BotFather) on Telegram,
     send `/newbot`, and follow the prompts. It will give you a token like
     `123456789:AAExampleTokenText`.
   - **OWNER_ID**: your numeric Telegram user id. Message
     [@userinfobot](https://t.me/userinfobot) and it will reply with your id.
   - **DB_PATH**: leave as `data/shop.db` unless you have a reason to change it.
   - **LOG_LEVEL**: leave as `INFO` unless debugging.
   - **PAYMENT_MODE**: `mock` (default, demo only) or `stars` — see [Payment modes](#payment-modes).
   - **SEED_DEMO**: `true` (default) loads a fictional demo catalogue into an empty database.

4. Run the bot:

   ```powershell
   python -m bot.main
   ```

   On first run this creates `data/shop.db` and applies the numbered migrations in
   `bot/migrations/`. With `SEED_DEMO=true` and no categories yet, it also loads the
   demo catalogue. Later runs apply only new migrations and never reseed.

## Payment modes

`PAYMENT_MODE` selects how checkout takes payment:

- **`stars`** — real Telegram Stars payments. The bot sends an invoice (currency `XTR`, no provider token). The order is only marked paid, and goods are only delivered, after Telegram's `successful_payment` update. A single order may not exceed 25,000 Stars (Telegram's invoice limit).
- **`mock`** — a demo. Checkout shows an order summary under a "DEMO MODE - no real payment" banner with a "Pay (demo)" button that runs the same completion path as a real payment, but no money moves.

> **Warning:** `mock` is a demo and must not be used in production. Anyone who can reach the bot can "pay" for anything and receive licence keys and files for free. Use `stars` for a real shop.

Orders keep the mode they were created in; an order from another mode is cancelled instead of resumed when the mode changes.

## How a purchase works

1. The customer adds editions to the cart and taps **Checkout**, reviews the order and taps **Pay**.
2. The bot creates the order, reserving licence keys for the reservation window (default 15 minutes, a setting) and clearing the cart, then presents the payment.
3. Telegram asks the bot to confirm (`pre_checkout_query`); the bot answers within seconds after checking the order is still payable.
4. On `successful_payment` the bot records the payment, tells the customer, and delivers each item (a message, a file, or licence keys) — in the background, retried every minute until it succeeds.
5. Purchases appear under **My Library**, where items can be sent again. A recurring sweep expires unpaid orders and releases their keys. A payment that arrives for an expired or already-paid order is refunded automatically.

The owner can run `/sweep` to trigger one sweep pass immediately; customers can use `/paysupport` for payment help.

## Admin guide

Everything below is done from the owner's account with `/admin`.

**Uploading files.** Open an edition of kind *File* in the catalogue manager and tap *Upload file* (or *Replace file*), then send the file as a **File** (paperclip → File), not as a photo or video, so Telegram keeps it unchanged. The bot stores only Telegram's `file_id` and the file name — nothing is downloaded. Replacing the file changes what existing buyers get when they re-download from their library. Telegram file ids are scoped to the bot, so **if you change the bot token you must upload every file again**.

**Adding licence keys.** Open an edition of kind *Key* and tap *Add keys*, then either paste the keys as a message or send a `.txt` file. One key per line; blank lines are ignored; spaces inside a key are fine. A line is rejected if it is longer than 200 characters or contains a control character. Duplicates (within the batch or already in stock) are skipped. At most 5000 keys per batch, and a `.txt` file may be at most 256 KB, UTF-8. The bot replies with counts and the numbers of rejected lines (at most ten) — it never repeats key text. The edition card shows stock as available / reserved / sold / revoked.

**Orders.** *Orders* lists orders by filter (*Needs attention* is paid orders with an undelivered item); `/order <id>` opens one directly. The order card shows the buyer, timestamps, items, payment details and recent events, with actions by status: *Cancel order* (pending; releases the reserved keys, the buyer's cart is not restored), *Retry delivery* (paid), *Resend to buyer* (delivered) and *Refund*.

**Refunds.** *Refund* returns the full amount to the buyer. For a Stars order Telegram is asked first, and the order is only marked refunded if that succeeds; a mock order never reaches Telegram. Refunding revokes the order's **sold licence keys** so they can never be sold again. Text and files that were already delivered **cannot be unsent**. The buyer is told. If Telegram refunds the money but the bot cannot record it, you get an alert with the charge id so you can reconcile by hand.

**Stats.** Real (Stars) and demo (mock) figures are always shown separately. For each window, *gross* is the total of orders paid in that window (including ones refunded since), *refunded* is the total of orders refunded in that window, and *net* is gross minus refunded. A refund counts in the window it happened in, so a refund of an older sale can make the last 7 days' net negative. Windows are the last 7 days and all time. Top editions are units sold from paid or delivered orders, per payment mode.

## Verification status

Stars mode: invoice creation has been checked against live Telegram. Pre-checkout, payment confirmation and refunds are covered by automated tests against python-telegram-bot's types but have not been exercised with real Stars. Mock mode exercises the same internal payment path end to end.

## Project layout

```
bot/
  main.py          entrypoint, logging, handler registration
  config.py        settings from the environment / .env
  db.py            connection and the numbered-migration runner
  migrations/      0001_init.sql, ... applied in filename order
  seed_demo.py     optional demo catalogue (seed_demo.json)
  delivery.py      sending purchased goods
  refunds.py       admin refunds (provider first, then the database)
  stats.py         the numbers behind the Stats screen
  keyparse.py      parsing pasted or uploaded licence keys
  redact.py        keeping the bot token out of logs
  jobs.py          the recurring sweep and /sweep
  payments/        Stars and demo providers, the shared payability check
  limits.py        shared limits (the 25,000 Star order cap)
  tg.py            typed access to optional update fields
  security.py      owner_only decorator
  render.py        HTML escaping and Stars formatting
  keyboards.py     reply keyboard and callback-data helpers
  i18n.py          customer-facing strings (t / tn) and the icon placeholders
  ui_kit.py        the design language: ICON, screen(), nav_row(), pager(), trimming
  summary.py       order / cart summary lines shared by checkout, payment and receipt
  profile.py       the bot's Telegram profile: command menus and descriptions
  repo/            query functions
  handlers/        command and callback handlers
data/              gitignored, created at runtime
```

## Design language

Every screen is built with `bot/ui_kit.py`: an icon and bold title, an optional breadcrumb and "Step N of M" line, then the body; the inline keyboard always ends with a nav row (Back, Home), except Home itself. Emoji come from one table (`ICON`), separators are fixed (` · ` between metadata, ` — ` between a label and its price), and lists are capped so the worst case stays under Telegram's 4096-character limit.

CUSTOMER-FACING text lives in `bot/i18n.py` behind `t()` and `tn()` (plurals). Admin screens are English literals in the handlers; only admin messages that contain a plural use `tn()`.

## Bot profile

On startup the bot sets its Telegram profile (`bot/profile.py`): the default command menu lists only customer commands (`/start`, `/library`, `/paysupport`); the owner's chat additionally lists `/admin`, `/order` and `/sweep`, plus the bot description and short description. This is best-effort: a failure is logged as a warning and never stops the bot. If the owner's menu could not be set because the owner has never opened the chat, it is retried the first time the owner sends `/start`.

## Development

```powershell
pip install -e ".[dev]"      # install the bot plus dev tools
pytest --cov --cov-fail-under=0   # run the tests with coverage
coverage report --omit="bot/handlers/*" --fail-under=80
coverage report --include="bot/handlers/*" --fail-under=60
ruff check . ; ruff format --check . ; mypy   # lint, format check, type check
```

Coverage measures line and branch coverage of the `bot` package by the tests. Handlers are measured (60% minimum) alongside everything else (80% minimum); `bot/main.py` is excluded from measurement.

Optional: `pre-commit install` runs ruff and basic file checks before each commit.
