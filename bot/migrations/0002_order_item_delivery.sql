-- Per-item delivery tracking: NULL until the item has been sent to the buyer.
ALTER TABLE order_items ADD COLUMN delivered_at TEXT;
