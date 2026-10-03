"""Optional demo catalogue (bot/seed_demo.json): fictional, obviously fake
content so a fresh checkout has something to browse. Runs only when
SEED_DEMO is on AND the categories table is empty, so it never touches a
catalogue someone has already built and is safe to call on every startup."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .config import get_settings
from .db import transaction

logger = logging.getLogger(__name__)

SEED_PATH = Path(__file__).resolve().parent / "seed_demo.json"


def seed_demo() -> bool:
    """Insert the demo catalogue. Returns True if it seeded, False if skipped."""
    if not get_settings().seed_demo:
        return False
    data = json.loads(SEED_PATH.read_text(encoding="utf-8"))

    with transaction() as conn:
        if conn.execute("SELECT 1 FROM categories LIMIT 1").fetchone() is not None:
            return False
        for cat_order, category in enumerate(data["categories"]):
            cat_id = conn.execute(
                "INSERT INTO categories (name, description, sort_order) VALUES (?, ?, ?)",
                (category["name"], category["description"], cat_order * 10),
            ).lastrowid
            for prod_order, product in enumerate(category["products"]):
                prod_id = conn.execute(
                    """
                    INSERT INTO products (category_id, name, description, sort_order)
                    VALUES (?, ?, ?, ?)
                    """,
                    (cat_id, product["name"], product["description"], prod_order * 10),
                ).lastrowid
                for var_order, variant in enumerate(product["variants"]):
                    var_id = conn.execute(
                        """
                        INSERT INTO product_variants
                            (product_id, label, price_stars, delivery_kind, delivery_text,
                             sort_order)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            prod_id,
                            variant["label"],
                            variant["price_stars"],
                            variant["delivery_kind"],
                            variant.get("delivery_text"),
                            var_order * 10,
                        ),
                    ).lastrowid
                    keys = [
                        f"NUMI-DEMO-{variant['key_prefix']}-{n:04d}"
                        for n in range(1, variant.get("key_count", 0) + 1)
                    ]
                    conn.executemany(
                        "INSERT INTO license_keys (variant_id, key_value) VALUES (?, ?)",
                        [(var_id, key) for key in keys],
                    )
    logger.info("Seeded demo catalogue")
    return True
