"""Shared SQLite connection handling for the bench adapters.

The default ``sqlite3.connect`` uses a 5 s busy timeout and a rollback journal (readers block the
writer), and ``with sqlite3.connect()`` never closes the connection. Concurrent FastAPI threadpool
polling then surfaces ``database is locked``. ``open_db`` gives a 30 s timeout and always closes.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from pathlib import Path

BUSY_TIMEOUT_S = 30
_log = logging.getLogger(__name__)
_wal_warned = False


@contextmanager
def open_db(path: str | Path, *, wal: bool = False) -> Generator[sqlite3.Connection]:
    """Open a connection; commit on success, roll back on error, and always close it.

    ``wal=True`` switches the database to WAL (persisted in the file, so call once at init). If the
    switch is refused (e.g. a read-only database) the rollback journal is kept and a warning is
    logged once.

    WAL is single-host only (no NFS), and a database already in WAL mode cannot be read from a
    read-only directory (it needs the -wal/-shm side files).
    """
    con = sqlite3.connect(path, timeout=BUSY_TIMEOUT_S)
    try:
        if wal:
            _enable_wal(con, path)
        with con:
            yield con
    finally:
        con.close()


def _enable_wal(con: sqlite3.Connection, path: str | Path) -> None:
    global _wal_warned
    try:
        con.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError as e:
        if not _wal_warned:
            _wal_warned = True
            _log.warning("could not enable WAL on %s (%s); keeping the rollback journal", path, e)


class SchemaTooNew(RuntimeError):
    """The database was written by a newer Bench than this one. Refused, never opened for writing."""


#: A schema step: one SQL statement, or a function of the connection. Steps run inside `migrate`'s transaction, so a
#: step must not commit; `executescript` does, which is why a SQL string is executed as a single statement.
Step = str | Callable[[sqlite3.Connection], None]


def add_column(con: sqlite3.Connection, table: str, column: str, kind: str) -> None:
    """`ALTER TABLE ... ADD COLUMN`, unless the column is already there. A database written before schemas were
    versioned (user_version 0) may hold any subset of the columns, so the steps that adopt it must be idempotent."""
    if column not in {r[1] for r in con.execute(f"PRAGMA table_info({table})")}:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")


def migrate(con: sqlite3.Connection, steps: Sequence[Step]) -> int:
    """Bring the database to version ``len(steps)``, applying only the steps above its ``PRAGMA user_version``.

    ``steps[0]`` makes version 1, ``steps[1]`` version 2, and so on: append, never edit or reorder, or a database
    that already ran the old step diverges from a fresh one. The steps and the version bump are one transaction, so
    a failing step leaves the database as it was. A fresh database runs every step; a current one is not written.
    A database whose version is NEWER than this build knows is refused with `SchemaTooNew`, before anything is
    written, because this build cannot know what the newer one changed.
    """
    target = len(steps)
    current = _user_version(con)
    if current == target:
        return target
    if current > target:
        raise SchemaTooNew(_too_new(con, current, target))
    try:
        con.execute("BEGIN IMMEDIATE")  # another process may be migrating too: take the lock, then look again
        current = _user_version(con)
        if current > target:
            raise SchemaTooNew(_too_new(con, current, target))
        for step in steps[current:]:
            step(con) if callable(step) else con.execute(step)
        con.execute(f"PRAGMA user_version = {target}")  # an int we computed: pragmas cannot take parameters
    except sqlite3.OperationalError as e:
        if "readonly" not in str(e).lower():
            raise
        # A read-only file (a copy kept for reading, a read-only mount) cannot be migrated, and a file written
        # before versioning may already have the current schema: serve it as it is rather than refuse it.
        con.rollback()
        _log.warning("database is read-only at schema version %d (this Bench is %d); not migrating it", current, target)
        return current
    return target


def _user_version(con: sqlite3.Connection) -> int:
    return con.execute("PRAGMA user_version").fetchone()[0]


def _too_new(con: sqlite3.Connection, found: int, known: int) -> str:
    row = con.execute("PRAGMA database_list").fetchone()
    where = row[2] if row and row[2] else "the database"
    return (
        f"{where} is schema version {found}, but this Bench understands up to {known}: it was written by a newer "
        "Bench. Upgrade Bench to open it. Do not edit it by hand; to start fresh instead, move the file aside "
        "(Bench makes a new one, and the old one stays intact for the newer Bench)."
    )
