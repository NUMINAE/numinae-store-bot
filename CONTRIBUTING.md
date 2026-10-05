# Contributing

Thanks for taking an interest. This is a small project; clear, tested changes are the
easiest to review.

## Setup

Python 3.11 or newer and SQLite 3.35 or newer.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows PowerShell: .\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
cp .env.example .env               # Windows PowerShell: Copy-Item .env.example .env
```

Fill in `.env` (a test bot token from @BotFather and your own Telegram user id). Never
commit `.env`.

## Checks

Run all of these before opening a pull request; CI runs the same:

```bash
pytest --cov --cov-fail-under=0
coverage report --omit="bot/handlers/*" --fail-under=80
coverage report --include="bot/handlers/*" --fail-under=60
ruff check .
ruff format --check .
mypy
```

CI (`.github/workflows/ci.yml`) also builds the Docker image and starts it under the
restrictions of `docker-compose.yml` with a fake token, so a change that makes the image
need a writable root filesystem or extra privileges fails there; you do not need Docker
locally to contribute.

New code needs tests. Tests never print or require real tokens; build token-shaped
strings from parts.

## Migrations

Schema changes are numbered files in `bot/migrations/` (`0006_short_name.sql`, ...),
applied in filename order. **Never edit a migration that has been applied or released:**
each file's checksum is stored in `schema_migrations` and an edited file stops the bot at
startup. Add a new migration instead, and add a test that a database at the previous
version upgrades and keeps its data.

## Adding a locale

Customer-facing text lives in `bot/i18n.py` behind `t()` and `tn()` (plurals), as one
flat table of keys. Admin screens are English literals in the handlers. To add a locale,
change `bot/i18n.py` and its call sites; handlers never format customer-facing text
themselves, and plurals always go through `tn()`.

## Project rules

- Order status changes only through `repo.orders.set_status`.
- Escape with `esc()` at render time, never before storage.
- Money is whole Stars, formatted only through `render.stars`.
- Callback data is under 64 bytes, built with `cb()` and parsed with `parse_callback` /
  `parse_cb_id`.
- Every order reached by id goes through `get_owned_order`; foreign and missing ids get
  the identical response.
- Build screens with `ui_kit.screen(...)` and end keyboards with `ui_kit.nav_row(...)`.
  Emoji come only from `ui_kit.ICON`.
- Trim raw text before escaping and size user-entered text by its escaped length; keep
  screens under 4096 characters. Paid content is split across messages, never cut.
- Never `await` inside a `with transaction()` / `with immediate_transaction()` block.
- Never log message text, key values, delivery text or update objects.
- Never print secrets; report counts and paths when searching for them.
- Ask before deviating from a spec, and report bugs that tests find instead of silently
  fixing them.
