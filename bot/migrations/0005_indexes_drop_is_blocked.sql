-- Two lookups scanned whole tables: "order_item_id IN (SELECT id FROM order_items ...)"
-- on license_keys (pre-checkout, payment, delivery) and "WHERE order_id = ?" on
-- order_events (the order card). Index both.
CREATE INDEX idx_license_keys_order_item ON license_keys(order_item_id);
CREATE INDEX idx_order_events_order ON order_events(order_id);

-- customers.is_blocked was never read or written by the bot. DROP COLUMN needs
-- SQLite 3.35 or newer (already required by 0004).
ALTER TABLE customers DROP COLUMN is_blocked;
