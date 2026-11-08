"""Shared SQLite connection handling for the bench adapters.

The default ``sqlite3.connect`` uses a 5 s busy timeout and a rollback journal (readers block the
writer), and ``with sqlite3.connect()`` never closes the connection. Concurrent FastAPI threadpool
polling then surfaces ``database is locked``. ``open_db`` gives a 30 s timeout and always closes.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Generator
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


def is_duplicate_column(exc: sqlite3.OperationalError) -> bool:
    return "duplicate column name" in str(exc).lower()
