"""The data directory and the database files are owner-only on POSIX."""

from __future__ import annotations

import logging
import os
import stat

import pytest

from bot import db as db_module
from bot.config import get_settings

posix_only = pytest.mark.skipif(
    os.name != "posix", reason="POSIX file modes do not exist on Windows"
)


def _mode(path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.fixture
def fresh_db_path(tmp_path, monkeypatch):
    db_module.close_conn()
    path = tmp_path / "nested" / "data" / "shop.db"
    monkeypatch.setenv("DB_PATH", str(path))
    get_settings.cache_clear()
    yield path
    db_module.close_conn()
    get_settings.cache_clear()


@posix_only
def test_a_new_data_directory_and_the_database_files_are_owner_only(fresh_db_path):
    old_umask = os.umask(0o022)  # a permissive umask must not weaken the modes
    try:
        db_module.init_db()
        conn = db_module.get_conn()
        conn.execute("INSERT INTO settings (key, value) VALUES ('probe', '1')")
        conn.commit()
    finally:
        os.umask(old_umask)

    assert _mode(fresh_db_path.parent) == 0o700
    assert _mode(fresh_db_path) == 0o600
    for suffix in ("-wal", "-shm"):
        sidecar = fresh_db_path.with_name(fresh_db_path.name + suffix)
        if sidecar.exists():
            assert _mode(sidecar) == 0o600, sidecar.name


@posix_only
def test_an_existing_directory_keeps_its_mode_but_the_database_is_still_restricted(
    fresh_db_path,
):
    fresh_db_path.parent.mkdir(parents=True)
    fresh_db_path.parent.chmod(0o750)  # deliberately looser than ours
    db_module.init_db()
    assert _mode(fresh_db_path.parent) == 0o750  # not ours to change
    assert _mode(fresh_db_path) == 0o600


def test_a_chmod_failure_never_stops_startup(fresh_db_path, monkeypatch, caplog):
    """Runs on every platform: the helper is called with enabled=True even on Windows."""
    caplog.set_level(logging.WARNING)
    fresh_db_path.parent.mkdir(parents=True)
    fresh_db_path.write_bytes(b"")

    def refuse(path, mode):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(db_module.os, "chmod", refuse)
    db_module._restrict_permissions(fresh_db_path, created_dir=True, enabled=True)  # no raise
    assert any("Could not restrict permissions" in r.getMessage() for r in caplog.records)


def test_a_failing_chmod_does_not_stop_get_conn(fresh_db_path, monkeypatch):
    def refuse(path, mode):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(db_module.os, "chmod", refuse)
    monkeypatch.setattr(db_module, "_restrict_permissions", _enabled_restrict(db_module))
    assert db_module.get_conn() is not None  # opens fine; permissions just stay as they were


def _enabled_restrict(module):
    original = module._restrict_permissions

    def forced(db_path, *, created_dir, enabled=True):
        return original(db_path, created_dir=created_dir, enabled=True)

    return forced


def test_the_helper_does_nothing_where_it_is_disabled(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(db_module.os, "chmod", lambda *a: called.append(a))
    target = tmp_path / "x.db"
    target.write_bytes(b"")
    db_module._restrict_permissions(target, created_dir=True, enabled=False)
    assert called == []
