"""SQLite comment store."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from smeltery.bench.ports.comment_store import Comment


class SqliteCommentStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _conn(self):
        return sqlite3.connect(self.db_path)

    def _init(self):
        with self._conn() as con:
            try:
                con.execute("ALTER TABLE comments ADD COLUMN run TEXT")
            except Exception:
                pass
            try:
                con.execute("ALTER TABLE comments ADD COLUMN anchor TEXT")
            except Exception:
                pass
        with self._conn() as con:
            con.execute(
                """CREATE TABLE IF NOT EXISTS comments (
                    id TEXT PRIMARY KEY, run TEXT, anchor_type TEXT, anchor_id TEXT, author TEXT,
                    body TEXT, created_at TEXT, parent_id TEXT, anchor TEXT)"""
            )
            con.commit()

    def add(self, anchor_type=None, anchor_id=None, author="", body="", *, parent_id=None, run=None, anchor=None, **kw):
        if anchor is not None and anchor_type is None and run is None:
            # bench style: run + anchor
            pass
        if run is not None and anchor_type is None:
            anchor_type = "run"
            anchor_id = run
        if anchor is None:
            anchor = kw.pop("anchor", None)
        if run is None:
            run = kw.pop("run", None)
        if body == "" and "body" in kw:
            body = kw.pop("body")
        if author == "" and "author" in kw:
            author = kw.pop("author")
        if anchor_type is None:
            anchor_type = kw.pop("anchor_type", "run")
        if anchor_id is None:
            anchor_id = kw.pop("anchor_id", run)
        if anchor is None:
            anchor = f"{anchor_type}:{anchor_id}" if anchor_type and anchor_id else (anchor_id or "")
        # bench style: if run given, store run and anchor string
        store_run = run
        if run is not None:
            anchor_type = "run"
            anchor_id = run
        cid = uuid.uuid4().hex[:12]
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self._conn() as con:
            con.execute(
                "INSERT INTO comments (id,run,anchor_type,anchor_id,author,body,created_at,parent_id,anchor) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (cid, store_run, anchor_type, anchor_id, author, body, now, parent_id, anchor),
            )
            con.commit()
        return Comment(
            id=cid,
            run=store_run,
            anchor_type=anchor_type,
            anchor_id=anchor_id,
            author=author,
            body=body,
            created_at=datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc),
            parent_id=parent_id,
            anchor_text=anchor,
        )

    def for_run(self, run):
        with self._conn() as con:
            rows = con.execute(
                "SELECT id,run,anchor_type,anchor_id,author,body,created_at,parent_id,anchor "
                "FROM comments WHERE run=? ORDER BY created_at",
                (run,),
            ).fetchall()
        out = []
        for r in rows:
            cid, runv, at, aid, author, body, created, parent, anch = r  # noqa: E501
            dt = datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            out.append(
                Comment(
                    id=cid,
                    run=runv,
                    anchor_type=at,
                    anchor_id=aid,
                    author=author,
                    body=body,
                    created_at=dt,
                    parent_id=parent,
                    anchor_text=anch or "",
                )
            )  # noqa: E501
        return out

    def list(self, anchor_type=None, anchor_id=None):
        q = "SELECT id,run,anchor_type,anchor_id,author,body,created_at,parent_id,anchor FROM comments"
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
        out = []
        for r in rows:
            cid, runv, at, aid, author, body, created, parent, anch = r  # noqa: E501
            dt = datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            out.append(
                Comment(
                    id=cid,
                    run=runv,
                    anchor_type=at,
                    anchor_id=aid,
                    author=author,
                    body=body,
                    created_at=dt,
                    parent_id=parent,
                    anchor_text=anch or "",
                )
            )  # noqa: E501
        return out

    def get(self, comment_id):
        with self._conn() as con:
            row = con.execute(
                "SELECT id,run,anchor_type,anchor_id,author,body,created_at,parent_id,anchor FROM comments WHERE id=?",
                (comment_id,),
            ).fetchone()
        if not row:
            return None
        cid, runv, at, aid, author, body, created, parent, anch = row
        dt = datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return Comment(
            id=cid,
            run=runv,
            anchor_type=at,
            anchor_id=aid,
            author=author,
            body=body,
            created_at=dt,
            parent_id=parent,
            anchor_text=anch or "",
        )  # noqa: E501
