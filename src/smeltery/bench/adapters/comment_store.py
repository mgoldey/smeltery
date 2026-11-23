"""SQLite comment store."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from smeltery.bench.adapters._sqlite import add_column, migrate, open_db
from smeltery.bench.ports.clock import Clock, SystemClock
from smeltery.bench.ports.comment_store import Comment
from smeltery.decisions import TIME_FORMAT


def _add_comment_columns(con) -> None:
    for col in ("run", "anchor"):
        add_column(con, "comments", col, "TEXT")


#: Schema steps; append only (see `_sqlite.migrate`). Version 2 adds the columns an older database lacks.
_STEPS = (
    """CREATE TABLE IF NOT EXISTS comments (
        id TEXT PRIMARY KEY, anchor_type TEXT, anchor_id TEXT, author TEXT,
        body TEXT, created_at TEXT, parent_id TEXT)""",
    _add_comment_columns,
)
SCHEMA_VERSION = len(_STEPS)


#: The column order `_comment` unpacks; every SELECT and the INSERT use it.
_COLUMNS = "id,run,anchor_type,anchor_id,author,body,created_at,parent_id,anchor"


def _comment(row) -> Comment:
    cid, run, anchor_type, anchor_id, author, body, created, parent, anchor = row
    return Comment(
        id=cid,
        run=run,
        anchor_type=anchor_type,
        anchor_id=anchor_id,
        author=author,
        body=body,
        created_at=datetime.strptime(created, TIME_FORMAT).replace(tzinfo=timezone.utc),
        parent_id=parent,
        anchor_text=anchor or "",
    )


class SqliteCommentStore:
    def __init__(self, db_path: str | Path, clock: Clock | None = None):
        self._clock = clock or SystemClock()
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _conn(self):
        return open_db(self.db_path)

    def _init(self):
        with open_db(self.db_path, wal=True) as con:
            migrate(con, _STEPS)

    def add(self, *, run, author, body, anchor=None, parent_id=None):
        if anchor is None:
            anchor = f"run:{run}"
        cid = uuid.uuid4().hex[:12]
        now = self._clock.now().strftime(TIME_FORMAT)
        with self._conn() as con:
            con.execute(
                f"INSERT INTO comments ({_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?)",
                (cid, run, "run", run, author, body, now, parent_id, anchor),
            )
            con.commit()
        return Comment(
            id=cid,
            run=run,
            anchor_type="run",
            anchor_id=run,
            author=author,
            body=body,
            created_at=datetime.strptime(now, TIME_FORMAT).replace(tzinfo=timezone.utc),
            parent_id=parent_id,
            anchor_text=anchor,
        )

    def for_run(self, run):
        with self._conn() as con:
            rows = con.execute(f"SELECT {_COLUMNS} FROM comments WHERE run=? ORDER BY created_at", (run,)).fetchall()
        return [_comment(r) for r in rows]

    def list(self, anchor_type=None, anchor_id=None):
        q = f"SELECT {_COLUMNS} FROM comments"
        params = []
        conds = []
        if anchor_type is not None:
            conds.append("anchor_type=?")
            params.append(anchor_type)
        if anchor_id is not None:
            conds.append("anchor_id=?")
            params.append(anchor_id)
        if conds:
            q += " WHERE " + " AND ".join(conds)
        q += " ORDER BY created_at"
        with self._conn() as con:
            rows = con.execute(q, params).fetchall()
        return [_comment(r) for r in rows]

    def get(self, comment_id):
        with self._conn() as con:
            row = con.execute(f"SELECT {_COLUMNS} FROM comments WHERE id=?", (comment_id,)).fetchone()
        return _comment(row) if row else None
