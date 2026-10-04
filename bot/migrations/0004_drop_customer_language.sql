-- The bot is English-only; the per-customer language setting is gone.
-- DROP COLUMN needs SQLite 3.35 or newer.
ALTER TABLE customers DROP COLUMN language;
