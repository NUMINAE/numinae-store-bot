PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_pending_input (
  user_id       INTEGER PRIMARY KEY,
  action        TEXT NOT NULL,      -- 'cat_name', 'prod_price', etc.
  target_id     INTEGER,            -- integer-keyed target (category/product/variant/order id); NULL when target_key is used instead
  target_key    TEXT,               -- string-keyed target (payment method code, setting key); NULL when target_id is used instead
  context_id    INTEGER,            -- parent id when creating, e.g. category for a new product
  pending_value TEXT,               -- a value already collected earlier in a multi-step flow
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS categories (
  id          INTEGER PRIMARY KEY,
  name        TEXT    NOT NULL,
  description TEXT    NOT NULL DEFAULT '',
  is_active   INTEGER NOT NULL DEFAULT 1,
  sort_order  INTEGER NOT NULL DEFAULT 0,
  created_at  TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS products (
  id          INTEGER PRIMARY KEY,
  category_id INTEGER NOT NULL REFERENCES categories(id),
  name        TEXT    NOT NULL,
  description TEXT    NOT NULL DEFAULT '',
  is_active   INTEGER NOT NULL DEFAULT 1,
  sort_order  INTEGER NOT NULL DEFAULT 0,
  created_at  TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS product_variants (
  id         INTEGER PRIMARY KEY,
  product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  label      TEXT    NOT NULL,          -- '100g', '250g', '500g'
  price_usd  INTEGER NOT NULL,          -- cents
  is_active  INTEGER NOT NULL DEFAULT 1,
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS payment_methods (
  code           TEXT PRIMARY KEY,         -- 'USDT_TRC20'
  asset          TEXT    NOT NULL,         -- 'USDT'
  asset_name     TEXT    NOT NULL,         -- 'Tether'
  network        TEXT    NOT NULL,         -- 'TRON (TRC-20)'
  wallet_address TEXT    NOT NULL DEFAULT '',
  memo_tag       TEXT,
  explorer_url   TEXT,                     -- template containing {tx}
  tx_hash_regex  TEXT,
  is_active      INTEGER NOT NULL DEFAULT 0,
  sort_order     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS customers (
  user_id            INTEGER PRIMARY KEY,  -- Telegram user id
  username           TEXT,
  first_name         TEXT,
  last_shipping_text TEXT,
  is_blocked         INTEGER NOT NULL DEFAULT 0,
  language           TEXT NOT NULL DEFAULT 'en',  -- Phase 6: 'en' | 'ru'
  created_at         TEXT NOT NULL DEFAULT (datetime('now')),
  last_seen_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS cart_items (
  user_id    INTEGER NOT NULL REFERENCES customers(user_id) ON DELETE CASCADE,
  variant_id INTEGER NOT NULL REFERENCES product_variants(id) ON DELETE CASCADE,
  quantity   INTEGER NOT NULL CHECK (quantity > 0),
  added_at   TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (user_id, variant_id)
);

CREATE TABLE IF NOT EXISTS orders (
  id              INTEGER PRIMARY KEY,
  user_id         INTEGER NOT NULL REFERENCES customers(user_id),
  total_usd       INTEGER NOT NULL,        -- cents
  item_count      INTEGER NOT NULL,
  payment_method  TEXT    NOT NULL REFERENCES payment_methods(code),
  wallet_address  TEXT    NOT NULL,        -- snapshot at order creation
  memo_tag        TEXT,
  status          TEXT    NOT NULL CHECK (status IN (
                    'awaiting_payment','awaiting_verification','awaiting_shipping',
                    'confirmed','shipped','delivered',
                    'rejected','cancelled','expired')),
  tx_hash         TEXT,
  tx_submitted_at TEXT,
  proof_file_id   TEXT,                    -- Phase 6: screenshot proof, Telegram file_id only
  reject_reason   TEXT,
  attempt_count   INTEGER NOT NULL DEFAULT 0,
  shipping_text   TEXT,
  tracking_note   TEXT,
  expires_at      TEXT,
  reminders_sent  INTEGER NOT NULL DEFAULT 0,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS order_items (
  id             INTEGER PRIMARY KEY,
  order_id       INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  variant_id     INTEGER REFERENCES product_variants(id) ON DELETE SET NULL,
  product_name   TEXT    NOT NULL,         -- snapshot
  variant_label  TEXT    NOT NULL,         -- snapshot, e.g. '250g'
  unit_price_usd INTEGER NOT NULL,         -- snapshot, cents
  quantity       INTEGER NOT NULL CHECK (quantity > 0),
  line_total_usd INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS order_events (
  id          INTEGER PRIMARY KEY,
  order_id    INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  from_status TEXT,
  to_status   TEXT NOT NULL,
  actor       TEXT NOT NULL,               -- 'customer' | 'owner' | 'system'
  note        TEXT,
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_variants_product  ON product_variants(product_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_products_category ON products(category_id, sort_order);

CREATE INDEX IF NOT EXISTS idx_orders_status     ON orders(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_orders_user       ON orders(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_orders_expiry     ON orders(status, expires_at);
CREATE INDEX IF NOT EXISTS idx_order_items_order ON order_items(order_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_txhash_live
  ON orders(payment_method, tx_hash)
  WHERE tx_hash IS NOT NULL
    AND status NOT IN ('rejected','cancelled','expired');
