# Numinae Store

A Telegram bot storefront for digital goods — files, licence keys and guides for creators and developers — paid with Telegram Stars.

**Status: work in progress.** Browsing, the cart, checkout (Telegram Stars or a demo mode), delivery and the customer library work; more admin tooling is still to come.

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

## Project layout

```
bot/
  main.py          entrypoint, logging, handler registration
  config.py        settings from the environment / .env
  db.py            connection and the numbered-migration runner
  migrations/      0001_init.sql, ... applied in filename order
  seed_demo.py     optional demo catalogue (seed_demo.json)
  delivery.py      sending purchased goods
  jobs.py          the recurring sweep and /sweep
  payments/        Stars and demo providers, the shared payability check
  limits.py        shared limits (the 25,000 Star order cap)
  tg.py            typed access to optional update fields
  security.py      owner_only decorator
  render.py        HTML escaping and Stars formatting
  keyboards.py     reply keyboard and callback-data helpers
  i18n.py          English / Russian customer strings
  repo/            query functions
  handlers/        command and callback handlers
data/              gitignored, created at runtime
```

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
