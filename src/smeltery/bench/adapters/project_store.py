"""SQLite project store."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from smeltery.bench.adapters._sqlite import open_db
from smeltery.project import Campaign, NotebookEntry, Proposal


class SqliteProjectStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _conn(self):
        return open_db(self.db_path)

    def _init(self):
        with open_db(self.db_path, wal=True) as con:
            con.execute(
                """CREATE TABLE IF NOT EXISTS campaigns (
                    id TEXT PRIMARY KEY, name TEXT, created_at TEXT, description TEXT, run_paths TEXT)"""
            )
            con.execute(
                """CREATE TABLE IF NOT EXISTS entries (
                    id TEXT PRIMARY KEY, campaign_id TEXT, title TEXT, body TEXT, author TEXT,
                    created_at TEXT, artefacts TEXT, supersedes TEXT)"""
            )
            con.execute(
                """CREATE TABLE IF NOT EXISTS proposals (
                    id TEXT PRIMARY KEY, run TEXT, smiles TEXT, reason TEXT, note TEXT, candidate TEXT,
                    author TEXT, created_at TEXT, status TEXT)"""
            )
            con.commit()

    def add_proposal(self, proposal: Proposal) -> Proposal:
        """Store a proposal for the computational chemist's review. Validated first; never launches anything."""
        proposal.validate()
        with self._conn() as con:
            con.execute(
                "INSERT INTO proposals (id,run,smiles,reason,note,candidate,author,created_at,status) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    proposal.id,
                    proposal.run,
                    proposal.smiles,
                    proposal.reason,
                    proposal.note,
                    proposal.candidate,
                    proposal.author,
                    proposal.created_at,
                    proposal.status,
                ),
            )
            con.commit()
        return proposal

    def list_proposals(self, run: str | None = None) -> list[Proposal]:
        q = "SELECT id,run,smiles,reason,note,candidate,author,created_at,status FROM proposals"
        args: tuple = ()
        if run is not None:
            q, args = q + " WHERE run=?", (run,)
        with self._conn() as con:
            got = con.execute(q + " ORDER BY created_at, id", args).fetchall()
        return [
            Proposal(
                id=r[0],
                run=r[1],
                smiles=r[2],
                reason=r[3],
                note=r[4],
                candidate=r[5],
                author=r[6],
                created_at=r[7],
                status=r[8],
            )
            for r in got
        ]

    def create_campaign(self, name, *, description=""):
        cid = uuid.uuid4().hex[:12]
        from smeltery.project import utc_now

        c = Campaign(id=cid, name=name, description=description, created_at=utc_now())
        c.validate()
        with self._conn() as con:
            con.execute(
                "INSERT INTO campaigns (id,name,created_at,description,run_paths) VALUES (?,?,?,?,?)",
                (c.id, c.name, c.created_at, c.description, json.dumps([])),
            )
            con.commit()
        return c

    def get_campaign(self, campaign_id):
        with self._conn() as con:
            row = con.execute(
                "SELECT id,name,created_at,description,run_paths FROM campaigns WHERE id=?", (campaign_id,)
            ).fetchone()
        if not row:
            raise KeyError(campaign_id)
        cid, name, created, desc, runs = row
        return Campaign(
            id=cid, name=name, created_at=created, description=desc or "", run_paths=json.loads(runs or "[]")
        )

    def list_campaigns(self):
        with self._conn() as con:
            rows = con.execute("SELECT id,name,created_at,description,run_paths FROM campaigns").fetchall()
        return [
            Campaign(id=r[0], name=r[1], created_at=r[2], description=r[3] or "", run_paths=json.loads(r[4] or "[]"))
            for r in rows
        ]

    def add_run(self, campaign_id, run_path):
        c = self.get_campaign(campaign_id)
        if str(run_path) not in c.run_paths:
            c.run_paths.append(str(run_path))
        with self._conn() as con:
            con.execute("UPDATE campaigns SET run_paths=? WHERE id=?", (json.dumps(c.run_paths), campaign_id))
            con.commit()

    def add_entry(self, entry=None, *args, **kw):
        # support port style (entry) and bench style (campaign_id, title=, body=, ...)
        if isinstance(entry, NotebookEntry):
            e = entry
        elif isinstance(entry, dict):
            e = NotebookEntry.from_dict(entry)
        elif entry is not None and (args or kw) and isinstance(entry, str) is not False:
            # entry may be campaign_id positional
            pass
        # bench style: first positional is campaign_id
        if not isinstance(entry, (NotebookEntry, dict)) and (kw or args):
            from smeltery.project import make_entry_id, utc_now

            campaign_id = entry if entry is not None else kw.pop("campaign_id")
            title = kw.pop("title", "")
            body = kw.pop("body", "")
            author = kw.pop("author", "")
            supersedes = kw.pop("supersedes", None)
            artefacts = kw.pop("artefacts", ())
            created_at = kw.pop("created_at", utc_now())
            eid = kw.pop("id", None) or make_entry_id(str(campaign_id), str(title), str(created_at))
            e = NotebookEntry(
                id=eid,
                campaign_id=str(campaign_id),
                title=title,
                body=body,
                author=author,
                created_at=created_at,
                artefacts=tuple(artefacts) if not isinstance(artefacts, tuple) else artefacts,
                supersedes=supersedes,
            )
        if isinstance(entry, dict):
            e = NotebookEntry.from_dict(entry)
        e.validate()
        # append-only: refuse editing an existing id
        with self._conn() as con:
            exists = con.execute("SELECT 1 FROM entries WHERE id=?", (e.id,)).fetchone()
            if exists:
                raise ValueError(f"entry {e.id} already exists; create a new entry that supersedes it")
            con.execute(
                "INSERT INTO entries (id,campaign_id,title,body,author,created_at,artefacts,supersedes) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    e.id,
                    e.campaign_id,
                    e.title,
                    e.body,
                    e.author,
                    e.created_at,
                    json.dumps(list(e.artefacts)),
                    e.supersedes,
                ),
            )
            con.commit()
        return e

    def entries(self, campaign_id):
        return self.list_entries(campaign_id)

    def list_entries(self, campaign_id):
        with self._conn() as con:
            rows = con.execute(
                "SELECT id,campaign_id,title,body,author,created_at,artefacts,supersedes "
                "FROM entries WHERE campaign_id=? ORDER BY created_at",
                (campaign_id,),
            ).fetchall()
        out = []
        for r in rows:
            out.append(
                NotebookEntry(
                    id=r[0],
                    campaign_id=r[1],
                    title=r[2],
                    body=r[3],
                    author=r[4],
                    created_at=r[5],
                    artefacts=tuple(json.loads(r[6] or "[]")),
                    supersedes=r[7],
                )
            )
        return out

    def supersede(self, old_entry_id, new_entry):
        ne = new_entry if isinstance(new_entry, NotebookEntry) else NotebookEntry.from_dict(new_entry)
        if ne.supersedes != old_entry_id:
            # allow setting
            ne = NotebookEntry(**{**ne.__dict__, "supersedes": old_entry_id}) if False else ne
        # ensure supersedes points to old
        if ne.supersedes != old_entry_id:
            d = ne.to_dict()
            d["supersedes"] = old_entry_id
            ne = NotebookEntry.from_dict(d)
        return self.add_entry(ne)
