## What and why

<!-- A short description of the change and the reason for it. -->

## Checklist

- [ ] Tests added or updated; no real tokens, keys or personal data in tests
- [ ] `pytest`, `ruff check .`, `ruff format --check .` and `mypy` pass; coverage thresholds hold
- [ ] Schema change? A new numbered migration, no applied migration edited, upgrade test added
- [ ] Order status changes go through `repo.orders.set_status`
- [ ] Dynamic text is escaped with `esc()` at render time; raw text is trimmed before escaping; screens stay under 4096 characters
- [ ] Callback data is under 64 bytes, built with `cb()`, parsed with `parse_callback` / `parse_cb_id`
- [ ] Orders and library items reached by id go through `get_owned_order`
- [ ] Screens use `ui_kit.screen` and `nav_row`; emoji come from `ICON`; customer-facing text is in `bot/i18n.py` (`t()` / `tn()`)
- [ ] No `await` inside a `with transaction()` block
- [ ] Nothing logs message text, key values, delivery text or update objects; no secrets printed
