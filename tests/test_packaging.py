"""The migrations and demo seed must ship with the package: bot/db.py finds
them next to its own module, so a wheel without them boots into a missing
schema. (CI additionally builds a wheel and inspects it.)"""

from __future__ import annotations

from importlib import resources

from bot import db as db_module
from bot.seed_demo import SEED_PATH


def test_migrations_are_found_via_importlib_resources_and_non_empty():
    migrations = resources.files("bot") / "migrations"
    assert migrations.is_dir()
    names = sorted(p.name for p in migrations.iterdir() if p.name.endswith(".sql"))
    assert names, "no migration files found in the package"
    assert names[0] == "0001_init.sql"


def test_the_runner_reads_the_same_directory():
    packaged = resources.files("bot") / "migrations"
    assert {p.name for p in db_module.MIGRATIONS_DIR.glob("*.sql")} == {
        p.name for p in packaged.iterdir() if p.name.endswith(".sql")
    }


def test_the_demo_seed_ships_with_the_package():
    assert (resources.files("bot") / "seed_demo.json").is_file()
    assert SEED_PATH.is_file()


def test_package_data_lists_migrations_and_the_demo_seed():
    import tomllib
    from pathlib import Path

    pyproject = tomllib.loads(
        (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    )
    patterns = pyproject["tool"]["setuptools"]["package-data"]["bot"]
    assert "migrations/*.sql" in patterns
    assert "seed_demo.json" in patterns
