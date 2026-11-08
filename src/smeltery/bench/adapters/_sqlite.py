"""Shared SQLite connection handling for the bench adapters.

The default ``sqlite3.connect`` uses a 5 s busy timeout and a rollback journal (readers block the
writer), and ``with sqlite3.connect()`` never closes the connection. Concurrent FastAPI threadpool
polling then surfaces ``database is locked``. ``open_db`` gives a 30 s timeout and always closes.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

BUSY_TIMEOUT_S = 30


@contextmanager
def open_db(path: str | Path, *, wal: bool = False) -> Generator[sqlite3.Connection]:
    """Open a connection; commit on success, roll back on error, and always close it.

    ``wal=True`` switches the database to WAL (persisted in the file, so call once at init).
    """
    con = sqlite3.connect(path, timeout=BUSY_TIMEOUT_S)
    try:
        if wal:
            con.execute("PRAGMA journal_mode=WAL")
        with con:
            yield con
    finally:
        con.close()


def is_duplicate_column(exc: sqlite3.OperationalError) -> bool:
    return "duplicate column name" in str(exc).lower()
