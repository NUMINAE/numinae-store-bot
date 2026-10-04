"""bot.backup: an online, integrity-checked, pruned copy of the database."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bot import backup
from bot import db as db_module
from bot.db import run_migrations

ROOT = Path(__file__).resolve().parent.parent
SECRET_KEY = "NUMI-BACKUP-KEY-0001"


def make_wal_db(path: Path) -> sqlite3.Connection:
    """A real, migrated, populated database in WAL mode. The returned connection
    stays open with its changes committed but not checkpointed, like a running bot."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    assert conn.execute("PRAGMA journal_mode = WAL").fetchone()[0] == "wal"
    conn.execute("PRAGMA wal_autocheckpoint = 0")  # keep the data in the -wal file
    run_migrations(conn, db_module.MIGRATIONS_DIR)
    conn.execute("INSERT INTO customers (user_id, username, first_name) VALUES (1, 'u', 'Ann')")
    conn.execute("INSERT INTO categories (name) VALUES ('C')")
    conn.execute("INSERT INTO products (category_id, name) VALUES (1, 'P')")
    conn.execute(
        "INSERT INTO product_variants (product_id, label, price_stars, delivery_kind)"
        " VALUES (1, 'V', 10, 'key')"
    )
    for n in range(25):
        conn.execute(
            "INSERT INTO license_keys (variant_id, key_value) VALUES (1, ?)",
            (f"{SECRET_KEY}-{n}",),
        )
    conn.execute(
        "INSERT INTO orders (user_id, total_stars, item_count, payment_mode, status)"
        " VALUES (1, 10, 1, 'mock', 'paid')"
    )
    conn.commit()
    return conn


def table_counts(path: Path) -> dict[str, int]:
    conn = sqlite3.connect(str(path))
    try:
        names = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        return {n: conn.execute(f"SELECT COUNT(*) FROM {n}").fetchone()[0] for n in names}  # noqa: S608 - names come from sqlite_master
    finally:
        conn.close()


# --- the backup itself --------------------------------------------------------------------------


def test_a_populated_wal_database_is_backed_up_with_identical_row_counts(tmp_path):
    source = tmp_path / "shop.db"
    live = make_wal_db(source)
    assert (tmp_path / "shop.db-wal").exists() and (tmp_path / "shop.db-wal").stat().st_size > 0

    target = backup.make_backup(source, tmp_path / "out")

    assert target.parent == tmp_path / "out" and backup.BACKUP_NAME.match(target.name)
    assert table_counts(target) == table_counts(source)
    assert table_counts(target)["license_keys"] == 25
    assert table_counts(target)["schema_migrations"] >= 5
    copy = sqlite3.connect(str(target))
    assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert (
        copy.execute(
            "SELECT COUNT(*) FROM license_keys WHERE key_value LIKE ?", (SECRET_KEY + "%",)
        ).fetchone()[0]
        == 25
    )  # plaintext, as documented
    copy.close()
    live.execute("INSERT INTO categories (name) VALUES ('later')")  # the bot keeps running
    live.commit()
    live.close()


def test_the_file_name_is_a_utc_timestamp(tmp_path):
    source = tmp_path / "shop.db"
    make_wal_db(source).close()
    moment = datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC)
    target = backup.make_backup(source, tmp_path / "out", now=moment)
    assert target.name == "shop-20260304-050607.db"


def test_the_default_output_directory_is_backups_beside_the_database(tmp_path, capsys):
    source = tmp_path / "data" / "shop.db"
    source.parent.mkdir()
    make_wal_db(source).close()
    assert backup.main(["--db", str(source)]) == 0
    (made,) = (source.parent / "backups").iterdir()
    assert backup.BACKUP_NAME.match(made.name)
    line = capsys.readouterr().out
    assert line == f"backup {made.name} {made.stat().st_size} bytes\n"  # name and size only


def test_the_source_database_is_not_modified(tmp_path):
    source = tmp_path / "shop.db"
    make_wal_db(source).close()
    before = table_counts(source)
    backup.make_backup(source, tmp_path / "out")
    assert table_counts(source) == before


def test_two_backups_in_the_same_second_do_not_overwrite(tmp_path):
    source = tmp_path / "shop.db"
    make_wal_db(source).close()
    moment = datetime(2026, 1, 1, tzinfo=UTC)
    backup.make_backup(source, tmp_path / "out", now=moment)
    with pytest.raises(backup.BackupError, match="already exists"):
        backup.make_backup(source, tmp_path / "out", now=moment)


# --- failures -----------------------------------------------------------------------------------


def test_a_missing_source_database_fails_cleanly_with_exit_1(tmp_path, capsys):
    out = tmp_path / "out"
    code = backup.main(["--db", str(tmp_path / "nope.db"), "--out", str(out)])
    assert code == 1
    assert capsys.readouterr().out == "backup failed: database not found\n"
    assert not out.exists()  # nothing was created


def test_a_corrupted_copy_is_deleted_and_reported(tmp_path, monkeypatch, capsys):
    source = tmp_path / "shop.db"
    make_wal_db(source).close()
    monkeypatch.setattr(backup, "_integrity_ok", lambda path: False)
    out = tmp_path / "out"
    assert backup.main(["--db", str(source), "--out", str(out)]) == 1
    output = capsys.readouterr().out
    assert output == "backup failed: integrity check failed on the copy; it was deleted\n"
    assert list(out.iterdir()) == []  # the bad copy is gone


def test_the_integrity_check_really_detects_a_damaged_file(tmp_path):
    source = tmp_path / "shop.db"
    make_wal_db(source).close()
    good = backup.make_backup(source, tmp_path / "out")
    assert backup._integrity_ok(good) is True
    data = bytearray(good.read_bytes())
    for offset in range(4096 * 2, len(data), 97):  # damage page data, keep the header
        data[offset] ^= 0xFF
    good.write_bytes(bytes(data))
    assert backup._integrity_ok(good) is False
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"not a database at all" * 100)
    assert backup._integrity_ok(junk) is False


def test_a_failure_during_the_copy_leaves_no_partial_file(tmp_path, monkeypatch):
    source = tmp_path / "shop.db"
    make_wal_db(source).close()
    real_connect = sqlite3.connect
    calls = {"n": 0}

    class Exploding:
        def __init__(self, conn):
            self._conn = conn

        def backup(self, destination):
            raise sqlite3.OperationalError("disk I/O error")

        def close(self):
            self._conn.close()

    def connect(*args, **kwargs):
        calls["n"] += 1
        conn = real_connect(*args, **kwargs)
        return Exploding(conn) if calls["n"] == 1 else conn  # the first is the source

    monkeypatch.setattr(backup.sqlite3, "connect", connect)
    with pytest.raises(backup.BackupError, match="backup failed"):
        backup.make_backup(source, tmp_path / "out")
    assert list((tmp_path / "out").iterdir()) == []


def test_keep_must_be_at_least_one(tmp_path):
    source = tmp_path / "shop.db"
    make_wal_db(source).close()
    with pytest.raises(backup.BackupError, match="--keep"):
        backup.make_backup(source, tmp_path / "out", keep=0)


# --- pruning ------------------------------------------------------------------------------------


def test_prune_keeps_the_newest_n_and_ignores_unrelated_files(tmp_path):
    names = [f"shop-2026010{d}-120000.db" for d in range(1, 8)]  # 7 backups
    for name in names:
        (tmp_path / name).write_text("x")
    unrelated = [
        "notes.txt",
        "shop.db",
        "shop-latest.db",
        "shop-20260101-120000.db.bak",
        "shop-2026-01-01.db",
        "my-shop-20260101-120000.db",
    ]
    for name in unrelated:
        (tmp_path / name).write_text("keep me")
    (tmp_path / "shop-20250101-000000.db.d").mkdir()  # a directory is never touched

    removed = backup.prune(tmp_path, keep=3)

    assert sorted(p.name for p in removed) == sorted(names[:4])  # the 4 oldest
    left = sorted(p.name for p in tmp_path.iterdir())
    assert all(name in left for name in names[4:])  # the newest 3 survive
    assert all(name in left for name in unrelated)  # nothing else was touched
    assert "shop-20250101-000000.db.d" in left


def test_prune_does_nothing_when_there_are_fewer_than_n(tmp_path):
    (tmp_path / "shop-20260101-120000.db").write_text("x")
    assert backup.prune(tmp_path, keep=14) == []


def test_make_backup_prunes_after_a_successful_copy(tmp_path):
    source = tmp_path / "shop.db"
    make_wal_db(source).close()
    out = tmp_path / "out"
    out.mkdir()
    for day in range(1, 6):
        (out / f"shop-2026010{day}-000000.db").write_text("old")
    (out / "other.txt").write_text("mine")
    backup.make_backup(source, out, keep=2, now=datetime(2026, 2, 1, tzinfo=UTC))
    left = sorted(p.name for p in out.iterdir())
    assert left == ["other.txt", "shop-20260105-000000.db", "shop-20260201-000000.db"]


def test_a_failed_backup_prunes_nothing(tmp_path, monkeypatch):
    source = tmp_path / "shop.db"
    make_wal_db(source).close()
    out = tmp_path / "out"
    out.mkdir()
    for day in range(1, 4):
        (out / f"shop-2026010{day}-000000.db").write_text("old")
    monkeypatch.setattr(backup, "_integrity_ok", lambda path: False)
    with pytest.raises(backup.BackupError):
        backup.make_backup(source, out, keep=1)
    assert len(list(out.iterdir())) == 3


# --- no token needed ------------------------------------------------------------------------------


def test_the_command_runs_without_a_token_or_any_other_setting(tmp_path):
    source = tmp_path / "shop.db"
    make_wal_db(source).close()
    env = {"PYTHONPATH": str(ROOT), "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")}
    proc = subprocess.run(
        [sys.executable, "-m", "bot.backup", "--db", str(source), "--out", str(tmp_path / "o")],
        cwd=tmp_path,  # no .env
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0 and proc.stderr == ""
    assert proc.stdout.startswith("backup shop-") and proc.stdout.count("\n") == 1
    assert str(tmp_path) not in proc.stdout  # no paths printed


def test_the_database_path_can_come_from_the_environment(tmp_path, monkeypatch, capsys):
    source = tmp_path / "from_env.db"
    make_wal_db(source).close()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DB_PATH", str(source))
    assert backup.main(["--out", str(tmp_path / "o")]) == 0
    assert capsys.readouterr().out.startswith("backup shop-")
    assert not backup.BACKUP_NAME.match("from_env.db")


def test_the_backup_and_a_new_output_directory_are_restricted_to_the_owner(tmp_path, monkeypatch):
    """Platform-independent: records the chmod calls rather than reading POSIX modes."""
    calls = []
    monkeypatch.setattr(backup, "_chmod", lambda path, mode: calls.append((path, mode)))
    source = tmp_path / "shop.db"
    make_wal_db(source).close()
    target = backup.make_backup(source, tmp_path / "out")
    assert calls == [(tmp_path / "out", 0o700), (target, 0o600)]


def test_a_chmod_failure_never_fails_the_backup(tmp_path, monkeypatch):
    def refuse(path, mode):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(backup.os, "chmod", refuse)
    monkeypatch.setattr(backup.os, "name", "posix")
    source = tmp_path / "shop.db"
    make_wal_db(source).close()
    assert backup.make_backup(source, tmp_path / "out").exists()
