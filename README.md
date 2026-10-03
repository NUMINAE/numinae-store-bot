# Persian Tea Shop Bot

A Telegram bot for a small online shop selling Persian tea, paid in crypto.
This is Phase 1: project foundation (config, database, logging, security,
rendering helpers, and stub handlers). Customer and admin flows are built in
later phases.

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

4. Run the bot:

   ```powershell
   python -m bot.main
   ```

   On first run this creates `data/shop.db`, applies the schema, and seeds
   starter settings, sample products, and payment method rows (all inactive
   until you add wallet addresses in a later phase). Subsequent runs will not
   duplicate the seed data.

## Project layout

```
tea_bot/
  bot/
    main.py          entrypoint, logging, handler registration
    config.py        .env loading and validation
    db.py            connection, schema bootstrap, seeding
    schema.sql        database schema
    seed.json          starter data
    security.py       owner_only decorator
    render.py          HTML escaping and formatting helpers
    keyboards.py       inline keyboards
    repo/               query functions, filled in per phase
    handlers/            command/callback handlers
  data/                 gitignored, created at runtime
```

## Development

```powershell
pip install -e ".[dev]"      # install the bot plus dev tools
pytest --cov                 # run the tests with coverage
ruff check . ; ruff format --check . ; mypy   # lint, format check, type check
```

Coverage measures line and branch coverage of the `bot` package by the tests, and `bot/handlers/`, `bot/jobs.py` and `bot/main.py` are excluded from measurement (as is `bot/repo/payment_methods.py`), so the figure says nothing about those files.

Optional: `pre-commit install` runs ruff and basic file checks before each commit.
