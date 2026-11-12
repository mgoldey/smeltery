import pytest

from bench.contracts.base import PortContract
from smeltery.bench.adapters.comment_store import SqliteCommentStore
from smeltery.bench.ports import CommentStore


class CommentStoreContract(PortContract):
    port = CommentStore

    @classmethod
    def adapters(cls):
        return [("SqliteCommentStore", lambda tmp: SqliteCommentStore(tmp / "comments.sqlite"))]


@pytest.fixture(params=CommentStoreContract.adapters(), ids=[n for n, _ in CommentStoreContract.adapters()])
def cstore(request, bench_tmp):
    return request.param[1](bench_tmp)


def test_a_comment_anchors_to_a_run_and_reads_back(cstore):
    cstore.add(run="abc123", anchor="candidate:4-F", author="mg", body="nitro on an aniline")
    got = cstore.for_run("abc123")
    assert [c.body for c in got] == ["nitro on an aniline"]
    assert got[0].anchor == "candidate:4-F"


def test_comments_for_an_unknown_run_are_empty_not_an_error(cstore):
    assert cstore.for_run("nope") == []


def test_a_long_read_transaction_does_not_block_the_writer(tmp_path):
    """Under a rollback journal an open reader blocks the writer's commit until the 5 s default
    busy timeout (the old "database is locked"); with WAL the writer is unaffected."""
    import sqlite3
    import threading

    from smeltery.bench.adapters.comment_store import SqliteCommentStore

    db = tmp_path / "c.sqlite"
    store = SqliteCommentStore(db)
    store.add(run="r1", author="a", body="seed")
    holding, release = threading.Event(), threading.Event()

    def hold_read():
        con = sqlite3.connect(db, isolation_level=None)
        try:
            con.execute("BEGIN")
            con.execute("SELECT * FROM comments").fetchall()  # takes the read lock
            holding.set()
            release.wait(timeout=6)  # longer than the old 5 s busy timeout
        finally:
            con.close()

    t = threading.Thread(target=hold_read)
    t.start()
    assert holding.wait(timeout=10)
    try:
        for i in range(5):
            store.add(run="r1", author="a", body=f"c{i}")
        assert len(store.for_run("r1")) == 6
    finally:
        release.set()
        t.join()


def test_a_failing_migration_step_is_reraised_not_swallowed(tmp_path, monkeypatch):
    import sqlite3

    import pytest

    from smeltery.bench.adapters import comment_store
    from smeltery.bench.adapters._sqlite import open_db as real_open_db

    db = tmp_path / "c.sqlite"
    con = sqlite3.connect(db)  # a database from before schemas were versioned: it still needs the ALTERs
    with con:
        con.execute(
            "CREATE TABLE comments (id TEXT PRIMARY KEY, anchor_type TEXT, anchor_id TEXT, author TEXT, "
            "body TEXT, created_at TEXT, parent_id TEXT)"
        )
    con.close()

    class Boom:
        def __init__(self, con):
            self._con = con

        def execute(self, sql, *a):
            if sql.startswith("ALTER"):
                raise sqlite3.OperationalError("database is locked")
            return self._con.execute(sql, *a)

    from contextlib import contextmanager

    @contextmanager
    def patched(path, **kw):
        with real_open_db(path, **kw) as con:
            yield Boom(con)

    monkeypatch.setattr(comment_store, "open_db", patched)
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        comment_store.SqliteCommentStore(db)


def test_a_read_only_rollback_journal_db_still_opens_and_lists(tmp_path):
    import os
    import sqlite3

    if os.geteuid() == 0:
        pytest.skip("root ignores file modes")
    db = tmp_path / "c.sqlite"
    # created the old way: rollback journal, no WAL
    with sqlite3.connect(db) as con:
        con.execute(
            "CREATE TABLE comments (id TEXT PRIMARY KEY, run TEXT, anchor_type TEXT, anchor_id TEXT, "
            "author TEXT, body TEXT, created_at TEXT, parent_id TEXT, anchor TEXT)"
        )
    con.close()
    db.chmod(0o444)
    try:
        store = SqliteCommentStore(db)
        assert store.list() == []
    finally:
        db.chmod(0o644)
