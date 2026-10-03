"""Single module-level SQLite connection and the numbered-migration runner.

sqlite3 is synchronous but the volume here is a few orders a day with
sub-millisecond queries, so a dedicated async driver isn't warranted. The
connection is created once with check_same_thread=False so PTB's event loop
(which runs handlers on one thread but not necessarily always the same thread
across awaits) can reuse it safely; callers are still responsible for not
interleaving writes across concurrent coroutines in ways that would corrupt
state, which the single-owner bot process here doesn't do.

Schema changes are numbered files in bot/migrations/ (0001_init.sql, ...),
applied in filename order, each in its own transaction and recorded in
schema_migrations with a checksum. Applied files are immutable: editing one is
an error, never silently accepted — add a new migration instead.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import re
import sqlite3
from collections.abc import Iterator
from pathlib import Path

from .config import get_settings

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
_MIGRATION_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")

_conn: sqlite3.Connection | None = None


class MigrationError(Exception):
    """A migration failed, was tampered with, or the migrations directory is
    inconsistent. Startup must abort; nothing is auto-fixed."""


def get_conn() -> sqlite3.Connection:
    """Return the module-level connection, creating it on first use."""
    global _conn
    if _conn is None:
        db_path = get_settings().db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(db_path), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA foreign_keys = ON")
        _conn.execute("PRAGMA journal_mode = WAL")
    return _conn


def close_conn() -> None:
    """Close and forget the module-level connection so the next get_conn()
    reopens it. Lets tests swap in a temporary database file."""
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None


@contextlib.contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """Context manager: commits on success, rolls back on exception."""
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def init_db() -> list[str]:
    """Apply any pending migrations; returns the versions applied this call."""
    return run_migrations(get_conn(), MIGRATIONS_DIR)


def _checksum(sql: str) -> str:
    # Line endings are normalised so a Windows checkout (autocrlf) and a Linux
    # one agree on the checksum of the same file.
    return hashlib.sha256(sql.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def _discover(migrations_dir: Path) -> list[Path]:
    files = sorted(p for p in migrations_dir.iterdir() if p.suffix == ".sql")
    seen: dict[str, str] = {}
    for path in files:
        match = _MIGRATION_NAME.match(path.name)
        if match is None:
            raise MigrationError(
                f"Migration file {path.name!r} is not named like '0001_description.sql'."
            )
        if match.group(1) in seen:
            raise MigrationError(
                f"Migration files {seen[match.group(1)]!r} and {path.name!r} share number "
                f"{match.group(1)}."
            )
        seen[match.group(1)] = path.name
    return files


def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
    ).fetchone()
    if exists is None:
        conn.execute(
            """
            CREATE TABLE schema_migrations (
                version    TEXT PRIMARY KEY,
                checksum   TEXT NOT NULL,
                applied_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.commit()


def run_migrations(conn: sqlite3.Connection, migrations_dir: Path) -> list[str]:
    """Apply unapplied migration files in filename order. Re-running is a
    no-op. A changed checksum on an already-applied file, or an applied file
    that has gone missing, raises MigrationError. A failing file is rolled back
    on its own (earlier files stay applied) and aborts the run. Migration files
    must not contain their own BEGIN/COMMIT."""
    files = _discover(migrations_dir)
    _ensure_migrations_table(conn)
    applied = {
        row["version"]: row["checksum"]
        for row in conn.execute("SELECT version, checksum FROM schema_migrations")
    }

    on_disk = {p.name for p in files}
    for version in applied:
        if version not in on_disk:
            raise MigrationError(
                f"Migration {version!r} is recorded as applied but its file is missing."
            )

    newly_applied: list[str] = []
    for path in files:
        sql = path.read_text(encoding="utf-8")
        checksum = _checksum(sql)
        if path.name in applied:
            if applied[path.name] != checksum:
                raise MigrationError(
                    f"Migration {path.name!r} was already applied but its contents have "
                    "changed. Applied migrations are immutable; add a new migration instead."
                )
            continue
        try:
            # executescript() commits first and runs statements in autocommit
            # mode, so the explicit BEGIN is what makes the file atomic. The
            # transaction is left open for the bookkeeping insert below.
            conn.executescript("BEGIN;\n" + sql)
            conn.execute(
                "INSERT INTO schema_migrations (version, checksum) VALUES (?, ?)",
                (path.name, checksum),
            )
            conn.commit()
        except Exception as exc:
            if conn.in_transaction:
                conn.rollback()
            raise MigrationError(
                f"Migration {path.name!r} failed and was rolled back: {exc}"
            ) from exc
        logger.info("Applied migration %s", path.name)
        newly_applied.append(path.name)
    return newly_applied
