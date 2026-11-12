"""B12: sqlite schemas are versioned with PRAGMA user_version, and a database from a newer build is refused."""

from __future__ import annotations

import sqlite3

import pytest

from smeltery.bench.adapters import comment_store, project_store, run_store, task_queue
from smeltery.bench.adapters._sqlite import SchemaTooNew, add_column, migrate, open_db
from smeltery.bench.adapters.comment_store import SqliteCommentStore
from smeltery.bench.adapters.project_store import SqliteProjectStore
from smeltery.bench.adapters.run_store import FilesystemRunStore
from smeltery.bench.adapters.task_queue import SubprocessTaskQueue


def version(path) -> int:
    con = sqlite3.connect(path)
    try:
        return con.execute("PRAGMA user_version").fetchone()[0]
    finally:
        con.close()


def schema(path) -> dict[str, list[tuple]]:
    """Every table's columns in order (name, type, notnull, default, pk): the shape, not the contents."""
    con = sqlite3.connect(path)
    try:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {t: [tuple(r[1:]) for r in con.execute(f"PRAGMA table_info({t})")] for t in tables}
    finally:
        con.close()


# ------------------------------------------------------------------------------------------- migrate()


STEPS = [
    "CREATE TABLE t (a TEXT)",
    lambda con: add_column(con, "t", "b", "TEXT"),
    lambda con: add_column(con, "t", "c", "INTEGER"),
]


def test_a_fresh_database_gets_every_step_and_the_version(tmp_path):
    db = tmp_path / "x.sqlite"
    with open_db(db) as con:
        assert migrate(con, STEPS) == 3
    assert version(db) == 3
    assert [c[0] for c in schema(db)["t"]] == ["a", "b", "c"]


def test_an_old_database_migrates_forward_applying_only_the_newer_steps(tmp_path):
    db = tmp_path / "x.sqlite"
    with open_db(db) as con:
        migrate(con, STEPS[:2])
        con.execute("INSERT INTO t (a,b) VALUES ('keep','me')")
    assert version(db) == 2
    calls = []
    steps = [STEPS[0], STEPS[1], lambda con: (calls.append("c"), add_column(con, "t", "c", "INTEGER"))]
    with open_db(db) as con:
        migrate(con, steps)
    assert calls == ["c"] and version(db) == 3
    con = sqlite3.connect(db)
    assert con.execute("SELECT a,b,c FROM t").fetchall() == [("keep", "me", None)]
    con.close()


def test_an_already_current_database_is_a_no_op(tmp_path):
    db = tmp_path / "x.sqlite"
    with open_db(db) as con:
        migrate(con, STEPS)
    ran = []
    with open_db(db) as con:
        assert migrate(con, [lambda c: ran.append(1)] * 3) == 3
    assert ran == [] and version(db) == 3


def test_a_newer_database_is_refused_with_an_actionable_message_and_left_alone(tmp_path):
    db = tmp_path / "x.sqlite"
    with open_db(db) as con:
        migrate(con, STEPS)
        con.execute("PRAGMA user_version = 9")
    with pytest.raises(SchemaTooNew) as ei:
        with open_db(db) as con:
            migrate(con, STEPS)
    msg = str(ei.value)
    assert str(db) in msg and "version 9" in msg and "up to 3" in msg
    assert "newer" in msg and "upgrade" in msg.lower()
    assert version(db) == 9  # never downgraded or touched


def test_a_failing_step_rolls_back_the_whole_migration(tmp_path):
    db = tmp_path / "x.sqlite"

    def boom(con):
        raise RuntimeError("no")

    with pytest.raises(RuntimeError):
        with open_db(db) as con:
            migrate(con, [STEPS[0], STEPS[1], boom])
    assert version(db) == 0 and schema(db) == {}


def test_add_column_is_idempotent(tmp_path):
    with open_db(tmp_path / "x.sqlite") as con:
        con.execute("CREATE TABLE t (a TEXT)")
        add_column(con, "t", "b", "TEXT")
        add_column(con, "t", "b", "TEXT")
        assert [r[1] for r in con.execute("PRAGMA table_info(t)")] == ["a", "b"]


# ---------------------------------------------------------- the stores: old database == fresh database

OLD_TASKS = """CREATE TABLE tasks (
    id TEXT PRIMARY KEY, name TEXT, command TEXT, cwd TEXT, status TEXT,
    created_at TEXT, started_at TEXT, finished_at TEXT, returncode INTEGER,
    log_path TEXT, note TEXT, pid INTEGER)"""
OLD_COMMENTS = """CREATE TABLE comments (
    id TEXT PRIMARY KEY, anchor_type TEXT, anchor_id TEXT, author TEXT, body TEXT, created_at TEXT, parent_id TEXT)"""
OLD_PROJECTS = [
    "CREATE TABLE campaigns (id TEXT PRIMARY KEY, name TEXT, created_at TEXT, description TEXT, run_paths TEXT)",
    """CREATE TABLE entries (id TEXT PRIMARY KEY, campaign_id TEXT, title TEXT, body TEXT, author TEXT,
       created_at TEXT, artefacts TEXT, supersedes TEXT)""",
]


def _legacy(path, ddl):
    con = sqlite3.connect(path)
    with con:
        for stmt in ddl:
            con.execute(stmt)
    con.close()


def test_task_queue_old_database_ends_at_the_fresh_schema_and_keeps_its_rows(tmp_path):
    (tmp_path / "old").mkdir()
    _legacy(
        tmp_path / "old" / "tasks.sqlite",
        [OLD_TASKS, "INSERT INTO tasks (id,name,command,status) VALUES ('t1','n','[\"true\"]','succeeded')"],
    )
    assert version(tmp_path / "old" / "tasks.sqlite") == 0
    old = SubprocessTaskQueue(tmp_path / "old")
    fresh = SubprocessTaskQueue(tmp_path / "fresh")
    assert schema(old.db) == schema(fresh.db)
    assert version(old.db) == version(fresh.db) == task_queue.SCHEMA_VERSION
    assert old.get("t1").status == "succeeded"


def test_task_queue_database_already_migrated_by_the_old_code_is_adopted(tmp_path):
    """The old code left user_version 0 on a fully migrated table: adopting it must not trip on existing columns."""
    (tmp_path / "old").mkdir()
    con = sqlite3.connect(tmp_path / "old" / "tasks.sqlite")
    con.execute(OLD_TASKS)
    for col, kind in task_queue._ADDED_COLUMNS:
        con.execute(f"ALTER TABLE tasks ADD COLUMN {col} {kind}")
    con.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    con.commit()
    con.close()
    old = SubprocessTaskQueue(tmp_path / "old")
    fresh = SubprocessTaskQueue(tmp_path / "fresh")
    assert schema(old.db) == schema(fresh.db) and version(old.db) == task_queue.SCHEMA_VERSION


def test_comment_store_old_database_ends_at_the_fresh_schema(tmp_path):
    _legacy(tmp_path / "old.sqlite", [OLD_COMMENTS, "INSERT INTO comments (id,body) VALUES ('c1','hi')"])
    old = SqliteCommentStore(tmp_path / "old.sqlite")
    fresh = SqliteCommentStore(tmp_path / "fresh.sqlite")
    assert schema(old.db_path) == schema(fresh.db_path)
    assert version(old.db_path) == version(fresh.db_path) == comment_store.SCHEMA_VERSION
    con = sqlite3.connect(old.db_path)
    assert con.execute("SELECT body FROM comments").fetchall() == [("hi",)]
    con.close()


def test_project_store_old_database_ends_at_the_fresh_schema(tmp_path):
    _legacy(tmp_path / "old.sqlite", OLD_PROJECTS)
    old = SqliteProjectStore(tmp_path / "old.sqlite")
    fresh = SqliteProjectStore(tmp_path / "fresh.sqlite")
    assert schema(old.db_path) == schema(fresh.db_path)
    assert version(old.db_path) == version(fresh.db_path) == project_store.SCHEMA_VERSION


def test_the_run_store_keeps_no_database(tmp_path):
    """B04 deleted the dead sqlite run index: the stat-keyed parse cache is the index, and the records are the only
    source of truth, so there is no second copy to version or migrate."""
    FilesystemRunStore(tmp_path).list_runs()
    assert [p.name for p in tmp_path.rglob("*.sqlite")] == []
    assert not hasattr(run_store, "SCHEMA_VERSION")


@pytest.mark.parametrize(
    "make",
    [
        lambda p: SubprocessTaskQueue(p / "q"),
        lambda p: SqliteCommentStore(p / "c.sqlite"),
        lambda p: SqliteProjectStore(p / "p.sqlite"),
    ],
    ids=["tasks", "comments", "projects"],
)
def test_every_store_refuses_a_newer_database(tmp_path, make):
    make(tmp_path)
    for db in [*tmp_path.rglob("*.sqlite")]:
        con = sqlite3.connect(db)
        con.execute("PRAGMA user_version = 99")
        con.close()
    with pytest.raises(SchemaTooNew, match="newer"):
        make(tmp_path)
