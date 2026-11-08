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


def test_concurrent_readers_do_not_lock_out_the_writer(tmp_path):
    import threading

    from smeltery.bench.adapters.comment_store import SqliteCommentStore

    db = tmp_path / "c.sqlite"
    store = SqliteCommentStore(db)
    n = 20
    stop = threading.Event()
    errors: list[Exception] = []

    def poll():
        s = SqliteCommentStore(db)
        while not stop.is_set():
            try:
                s.for_run("r1")
                s.list()
            except Exception as e:  # noqa: BLE001
                errors.append(e)
                return

    readers = [threading.Thread(target=poll) for _ in range(3)]
    for t in readers:
        t.start()
    try:
        for i in range(n):
            store.add(run="r1", author="a", body=f"c{i}")
    except Exception as e:  # noqa: BLE001
        errors.append(e)
    finally:
        stop.set()
        for t in readers:
            t.join()
    assert not errors
    assert len(store.for_run("r1")) == n


def test_migration_reraises_errors_other_than_duplicate_column(tmp_path, monkeypatch):
    import sqlite3

    import pytest

    from smeltery.bench.adapters import comment_store
    from smeltery.bench.adapters._sqlite import open_db as real_open_db

    db = tmp_path / "c.sqlite"
    comment_store.SqliteCommentStore(db)  # existing DB: duplicate-column path is swallowed

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
