# Numinae Store

A Telegram bot storefront for digital goods — files, licence keys and guides for creators and developers — paid with Telegram Stars.

**Status: work in progress.** Browsing the catalogue and the cart work; checkout, delivery and the customer library are not built yet (the checkout button and "My Library" are placeholders).

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
   - **PAYMENT_MODE**: `mock` (default) or `stars`.
   - **SEED_DEMO**: `true` (default) loads a fictional demo catalogue into an empty database.

4. Run the bot:

   ```powershell
   python -m bot.main
   ```

   On first run this creates `data/shop.db` and applies the numbered migrations in
   `bot/migrations/`. With `SEED_DEMO=true` and no categories yet, it also loads the
   demo catalogue. Later runs apply only new migrations and never reseed.

## Project layout

```
bot/
  main.py          entrypoint, logging, handler registration
  config.py        settings from the environment / .env
  db.py            connection and the numbered-migration runner
  migrations/      0001_init.sql, ... applied in filename order
  seed_demo.py     optional demo catalogue (seed_demo.json)
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

Coverage measures line and branch coverage of the `bot` package by the tests. Handlers are measured (60% minimum) alongside everything else (80% minimum); `bot/main.py` is excluded from measurement, and jobs no longer exist.

Optional: `pre-commit install` runs ruff and basic file checks before each commit.
