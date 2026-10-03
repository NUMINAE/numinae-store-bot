"""The numbered-migration runner, exercised against throwaway databases and
migration directories (plus the real schema through the shared fixtures)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from bot import db as db_module
from bot.db import MigrationError, run_migrations


@pytest.fixture
def conn(tmp_path: Path):
    connection = sqlite3.connect(str(tmp_path / "m.db"))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    yield connection
    connection.close()


@pytest.fixture
def mdir(tmp_path: Path) -> Path:
    path = tmp_path / "migrations"
    path.mkdir()
    return path


def _write(mdir: Path, name: str, sql: str) -> Path:
    path = mdir / name
    path.write_text(sql, encoding="utf-8")
    return path


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


@pytest.fixture
def pair(mdir: Path) -> Path:
    _write(mdir, "0001_a.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY);")
    _write(mdir, "0002_b.sql", "CREATE TABLE b (id INTEGER PRIMARY KEY);")
    return mdir


def test_applies_files_in_filename_order(conn, mdir):
    # Written out of order on purpose; 0002 depends on the table 0001 creates.
    _write(mdir, "0002_b.sql", "INSERT INTO a (id) VALUES (1);")
    _write(mdir, "0001_a.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY);")
    applied = run_migrations(conn, mdir)
    assert applied == ["0001_a.sql", "0002_b.sql"]
    assert conn.execute("SELECT COUNT(*) FROM a").fetchone()[0] == 1
    versions = [r["version"] for r in conn.execute("SELECT version FROM schema_migrations")]
    assert sorted(versions) == ["0001_a.sql", "0002_b.sql"]


def test_records_a_checksum_and_timestamp(conn, pair):
    run_migrations(conn, pair)
    row = conn.execute("SELECT * FROM schema_migrations WHERE version = '0001_a.sql'").fetchone()
    assert len(row["checksum"]) == 64
    assert row["applied_at"]


def test_rerun_is_a_noop(conn, pair):
    run_migrations(conn, pair)
    assert run_migrations(conn, pair) == []
    assert conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 2


def test_only_new_files_apply_on_a_later_run(conn, mdir):
    _write(mdir, "0001_a.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY);")
    run_migrations(conn, mdir)
    _write(mdir, "0002_b.sql", "CREATE TABLE b (id INTEGER PRIMARY KEY);")
    assert run_migrations(conn, mdir) == ["0002_b.sql"]


def test_changed_checksum_of_an_applied_file_is_detected_and_names_the_file(conn, pair):
    run_migrations(conn, pair)
    (pair / "0001_a.sql").write_text("CREATE TABLE a (id INTEGER PRIMARY KEY, x TEXT);")
    with pytest.raises(MigrationError, match=r"0001_a\.sql"):
        run_migrations(conn, pair)
    assert "x" not in {r["name"] for r in conn.execute("PRAGMA table_info(a)")}


def test_checksum_ignores_line_ending_style(conn, mdir):
    path = _write(mdir, "0001_a.sql", "CREATE TABLE a (id INTEGER);\n-- note\n")
    run_migrations(conn, mdir)
    path.write_bytes(b"CREATE TABLE a (id INTEGER);\r\n-- note\r\n")
    assert run_migrations(conn, mdir) == []


def test_failure_rolls_back_that_file_only_and_aborts(conn, mdir):
    _write(mdir, "0001_good.sql", "CREATE TABLE good (id INTEGER PRIMARY KEY);")
    _write(
        mdir,
        "0002_bad.sql",
        "CREATE TABLE half (id INTEGER PRIMARY KEY);\nINSERT INTO missing_table VALUES (1);",
    )
    _write(mdir, "0003_later.sql", "CREATE TABLE later (id INTEGER PRIMARY KEY);")
    with pytest.raises(MigrationError, match=r"0002_bad\.sql"):
        run_migrations(conn, mdir)
    tables = _tables(conn)
    assert "good" in tables  # earlier file stays applied
    assert "half" not in tables  # failing file fully rolled back
    assert "later" not in tables  # run aborted
    versions = [r["version"] for r in conn.execute("SELECT version FROM schema_migrations")]
    assert versions == ["0001_good.sql"]
    assert not conn.in_transaction


def test_a_fixed_file_applies_after_a_failure(conn, mdir):
    _write(mdir, "0001_x.sql", "SELECT * FROM nope;")
    with pytest.raises(MigrationError):
        run_migrations(conn, mdir)
    _write(mdir, "0001_x.sql", "CREATE TABLE x (id INTEGER PRIMARY KEY);")
    assert run_migrations(conn, mdir) == ["0001_x.sql"]


def test_an_applied_file_that_disappears_is_an_error(conn, pair):
    run_migrations(conn, pair)
    (pair / "0002_b.sql").unlink()
    with pytest.raises(MigrationError, match=r"0002_b\.sql"):
        run_migrations(conn, pair)


def test_badly_named_and_duplicate_numbers_are_rejected(conn, mdir):
    _write(mdir, "schema.sql", "SELECT 1;")
    with pytest.raises(MigrationError, match=r"schema\.sql"):
        run_migrations(conn, mdir)
    (mdir / "schema.sql").unlink()
    _write(mdir, "0001_a.sql", "SELECT 1;")
    _write(mdir, "0001_b.sql", "SELECT 1;")
    with pytest.raises(MigrationError, match="0001"):
        run_migrations(conn, mdir)


# --- the real schema --------------------------------------------------------


def test_real_migrations_apply_to_a_fresh_database_and_are_idempotent(empty_db):
    assert db_module.init_db() == []
    assert empty_db.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] >= 1


def test_connection_pragmas(empty_db):
    assert empty_db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert empty_db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_fresh_database_has_every_default_setting(empty_db):
    settings = {r["key"]: r["value"] for r in empty_db.execute("SELECT key, value FROM settings")}
    assert set(settings) == {
        "welcome_text",
        "support_note",
        "reservation_minutes",
        "notify_new_customers",
    }
    assert settings["reservation_minutes"] == "15"
    assert settings["notify_new_customers"] == "1"
    assert "Welcome to Numinae Store" in settings["welcome_text"]
    assert all(value for value in settings.values())


def test_no_migration_relies_on_if_not_exists():
    for path in db_module.MIGRATIONS_DIR.glob("*.sql"):
        assert "IF NOT EXISTS" not in path.read_text(encoding="utf-8").upper(), path.name
