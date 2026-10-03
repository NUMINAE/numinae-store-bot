-- Schema v1. All money is whole Telegram Stars (INTEGER). Timestamps are UTC
-- text from datetime('now'). Catalogue rows are never hard-deleted; they
-- deactivate via is_active.

CREATE TABLE settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

INSERT INTO settings (key, value) VALUES
  ('welcome_text', 'Welcome to Numinae Store — digital assets for creators and developers. Browse the catalogue and get your files, keys and guides instantly.'),
  ('support_note', 'Questions about a purchase? Message the shop owner and include your order number.'),
  ('reservation_minutes', '15'),
  ('notify_new_customers', '1');

CREATE TABLE customers (
  user_id      INTEGER PRIMARY KEY,
  username     TEXT,
  first_name   TEXT,
  language     TEXT NOT NULL DEFAULT 'en',
  is_blocked   INTEGER NOT NULL DEFAULT 0,
  created_at   TEXT NOT NULL DEFAULT (datetime('now')),
  last_seen_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE categories (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  is_active   INTEGER NOT NULL DEFAULT 1,
  sort_order  INTEGER NOT NULL DEFAULT 0,
  created_at  TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE products (
  id          INTEGER PRIMARY KEY,
  category_id INTEGER NOT NULL REFERENCES categories(id),
  name        TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  is_active   INTEGER NOT NULL DEFAULT 1,
  sort_order  INTEGER NOT NULL DEFAULT 0,
  created_at  TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE product_variants (
  id               INTEGER PRIMARY KEY,
  product_id       INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  label            TEXT NOT NULL,
  price_stars      INTEGER NOT NULL CHECK (price_stars > 0),
  delivery_kind    TEXT NOT NULL DEFAULT 'text' CHECK (delivery_kind IN ('file', 'key', 'text')),
  delivery_text    TEXT,
  delivery_file_id TEXT,
  is_active        INTEGER NOT NULL DEFAULT 1,
  sort_order       INTEGER NOT NULL DEFAULT 0,
  created_at       TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE cart_items (
  user_id    INTEGER NOT NULL REFERENCES customers(user_id) ON DELETE CASCADE,
  variant_id INTEGER NOT NULL REFERENCES product_variants(id) ON DELETE CASCADE,
  quantity   INTEGER NOT NULL CHECK (quantity > 0),
  added_at   TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (user_id, variant_id)
);

CREATE TABLE orders (
  id                         INTEGER PRIMARY KEY,
  user_id                    INTEGER NOT NULL REFERENCES customers(user_id),
  total_stars                INTEGER NOT NULL CHECK (total_stars > 0),
  item_count                 INTEGER NOT NULL,
  payment_mode               TEXT NOT NULL CHECK (payment_mode IN ('stars', 'mock')),
  status                     TEXT NOT NULL CHECK (status IN
    ('pending_payment', 'paid', 'delivered', 'cancelled', 'expired', 'refunded')),
  telegram_payment_charge_id TEXT,
  paid_at                    TEXT,
  delivered_at               TEXT,
  refunded_at                TEXT,
  expires_at                 TEXT,
  created_at                 TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at                 TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE order_items (
  id               INTEGER PRIMARY KEY,
  order_id         INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  variant_id       INTEGER REFERENCES product_variants(id) ON DELETE SET NULL,
  product_name     TEXT NOT NULL,
  variant_label    TEXT NOT NULL,
  delivery_kind    TEXT NOT NULL,
  unit_price_stars INTEGER NOT NULL,
  quantity         INTEGER NOT NULL CHECK (quantity > 0),
  line_total_stars INTEGER NOT NULL
);

CREATE TABLE order_events (
  id          INTEGER PRIMARY KEY,
  order_id    INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  from_status TEXT,
  to_status   TEXT NOT NULL,
  actor       TEXT NOT NULL,
  note        TEXT,
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE license_keys (
  id             INTEGER PRIMARY KEY,
  variant_id     INTEGER NOT NULL REFERENCES product_variants(id) ON DELETE CASCADE,
  key_value      TEXT NOT NULL,
  status         TEXT NOT NULL DEFAULT 'available'
    CHECK (status IN ('available', 'reserved', 'sold', 'revoked')),
  order_item_id  INTEGER REFERENCES order_items(id) ON DELETE SET NULL,
  reserved_until TEXT,
  created_at     TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (variant_id, key_value)
);

CREATE TABLE admin_pending_input (
  user_id       INTEGER PRIMARY KEY,
  action        TEXT NOT NULL,
  target_id     INTEGER,
  target_key    TEXT,
  context_id    INTEGER,
  pending_value TEXT,
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_orders_status       ON orders(status, created_at DESC);
CREATE INDEX idx_orders_user         ON orders(user_id, created_at DESC);
CREATE INDEX idx_order_items_order   ON order_items(order_id);
CREATE INDEX idx_variants_product    ON product_variants(product_id, sort_order);
CREATE INDEX idx_products_category   ON products(category_id, sort_order);
CREATE INDEX idx_license_keys_status ON license_keys(variant_id, status);

CREATE UNIQUE INDEX idx_orders_charge_id
  ON orders(telegram_payment_charge_id) WHERE telegram_payment_charge_id IS NOT NULL;
-- One open order per customer.
CREATE UNIQUE INDEX idx_orders_one_pending
  ON orders(user_id) WHERE status = 'pending_payment';
