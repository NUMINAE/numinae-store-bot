"""Back up the database: `python -m bot.backup [--db PATH] [--out DIR] [--keep N]`.

Uses SQLite's online backup API, which is safe while the bot is running and the
database is in WAL mode (a plain file copy is not). The copy is integrity-checked;
a bad copy is deleted and reported. Old backups beyond --keep are pruned, touching
only files that match the exact backup file-name pattern.

Needs no BOT_TOKEN and does not load the full Settings: the database path comes
from --db, or DB_PATH in the environment / .env, or the default (bot/dbpath.py).

The database holds licence keys in PLAINTEXT, so every backup does too: store them
encrypted and off the machine."""

from __future__ import annotations

import argparse
import contextlib
import os
import re
import sqlite3
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from .dbpath import resolve_db_path

DEFAULT_KEEP = 14
BACKUP_NAME = re.compile(r"^shop-\d{8}-\d{6}\.db$")  # the ONLY files ever pruned
FILE_MODE = 0o600
DIR_MODE = 0o700


class BackupError(Exception):
    """A backup could not be made; the message is one safe line."""


def backup_filename(moment: datetime) -> str:
    return f"shop-{moment.astimezone(UTC):%Y%m%d-%H%M%S}.db"


def _chmod(path: Path, mode: int) -> None:
    if os.name != "posix":
        return
    with contextlib.suppress(OSError):  # best effort: a refusing filesystem must not fail us
        os.chmod(path, mode)


def _integrity_ok(path: Path) -> bool:
    try:
        conn = sqlite3.connect(str(path))
        try:
            return bool(conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok")
        finally:
            conn.close()
    except Exception:  # a check that cannot even run (damaged pages) is a failed check
        return False


def prune(out_dir: Path, keep: int) -> list[Path]:
    """Delete the oldest backups beyond `keep`. Only regular files whose names
    match BACKUP_NAME exactly are considered; everything else is left alone."""
    mine = sorted(
        (p for p in out_dir.iterdir() if p.is_file() and BACKUP_NAME.match(p.name)),
        key=lambda p: p.name,  # the timestamp sorts lexically
        reverse=True,
    )
    removed = []
    for old in mine[keep:]:
        old.unlink()
        removed.append(old)
    return removed


def make_backup(
    db_path: Path,
    out_dir: Path,
    keep: int = DEFAULT_KEEP,
    *,
    now: datetime | None = None,
) -> Path:
    if keep < 1:
        raise BackupError("--keep must be at least 1")
    if not db_path.is_file():
        raise BackupError("database not found")
    created_dir = not out_dir.exists()
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BackupError(f"cannot create the output directory ({type(exc).__name__})") from None
    if created_dir:
        _chmod(out_dir, DIR_MODE)

    target = out_dir / backup_filename(now or datetime.now(UTC))
    if target.exists():
        raise BackupError("a backup with this timestamp already exists; wait a second")

    try:
        source = sqlite3.connect(str(db_path))
        try:
            destination = sqlite3.connect(str(target))
            try:
                source.backup(destination)  # consistent, even with WAL and a running bot
            finally:
                destination.close()
        finally:
            source.close()
    except sqlite3.Error as exc:
        target.unlink(missing_ok=True)
        raise BackupError(f"backup failed ({type(exc).__name__})") from None

    if not _integrity_ok(target):
        target.unlink(missing_ok=True)
        raise BackupError("integrity check failed on the copy; it was deleted")

    _chmod(target, FILE_MODE)
    prune(out_dir, keep)
    return target


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bot.backup")
    parser.add_argument("--db", help="database file (default: DB_PATH, else data/shop.db)")
    parser.add_argument("--out", help="output directory (default: <db dir>/backups)")
    parser.add_argument(
        "--keep", type=int, default=DEFAULT_KEEP, help=f"backups to keep (default {DEFAULT_KEEP})"
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    db_path = resolve_db_path(args.db)
    out_dir = Path(args.out) if args.out else db_path.parent / "backups"
    try:
        target = make_backup(db_path, out_dir, args.keep)
    except BackupError as exc:
        print(f"backup failed: {exc}")
        return 1
    print(f"backup {target.name} {target.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
