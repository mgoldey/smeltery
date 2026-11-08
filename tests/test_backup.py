import hashlib
import io
import json
import sqlite3
import tarfile
from pathlib import Path

import pytest

from smeltery import backup as bk
from smeltery.cli import main


def make_workspace(root: Path) -> Path:
    (root / "pipelines" / "p1").mkdir(parents=True)
    (root / "pipelines" / "p1" / "config.toml").write_text("name = 'p1'\n")
    (root / "methods.toml").write_text("[x]\n")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "junk.js").write_text("x")
    (root / "outside.txt").write_text("secret")
    (root / "link").symlink_to(root / "outside.txt")
    db = root / ".smeltery" / "bench"
    db.mkdir(parents=True)
    con = sqlite3.connect(db / "projects.sqlite")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE t(a, b)")
    con.executemany("INSERT INTO t VALUES (?, ?)", [(i, f"v{i}") for i in range(50)])
    con.commit()
    con.close()
    return root


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def rewrite(archive: Path, out: Path, edit):
    with tarfile.open(archive) as src, tarfile.open(out, "w:gz") as dst:
        for m in src.getmembers():
            data = src.extractfile(m).read() if m.isfile() else None
            m, data = edit(m, data)
            dst.addfile(m, io.BytesIO(data) if data is not None else None)


def test_round_trip(tmp_path, capsys):
    root = make_workspace(tmp_path / "ws")
    arc = tmp_path / "b.tar.gz"
    assert main(["backup", str(root), "--out", str(arc)]) == 0
    assert "skipped symlink link" in capsys.readouterr().err
    with tarfile.open(arc) as t:
        names = t.getnames()
        manifest = json.load(t.extractfile("MANIFEST.json"))
    assert not any("node_modules" in n or n.endswith(("-wal", "-shm")) or "link" in n for n in names)
    assert {"smeltery_version", "created_utc"} <= manifest.keys()
    dest = tmp_path / "new"
    assert main(["restore", str(arc), "--into", str(dest)]) == 0
    for rel in ("pipelines/p1/config.toml", "methods.toml", "outside.txt"):
        assert sha(dest / rel) == sha(root / rel) == manifest["files"][rel]["sha256"]
    rows = (
        sqlite3.connect(dest / ".smeltery/bench/projects.sqlite").execute("SELECT count(*), max(a) FROM t").fetchone()
    )
    assert rows == (50, 49)
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".smeltery-restore")] == []


def test_restore_into_empty_existing_dir_and_dry_run(tmp_path):
    arc = tmp_path / "b.tar.gz"
    bk.backup(make_workspace(tmp_path / "ws"), arc, warn=lambda m: None)
    dest = tmp_path / "empty"
    dest.mkdir()
    assert main(["restore", str(arc), "--into", str(dest), "--dry-run"]) == 0
    assert list(dest.iterdir()) == []
    assert main(["restore", str(arc), "--into", str(dest)]) == 0
    assert (dest / "methods.toml").is_file()


def test_restore_refuses_non_empty_target(tmp_path, capsys):
    arc = tmp_path / "b.tar.gz"
    bk.backup(make_workspace(tmp_path / "ws"), arc, warn=lambda m: None)
    dest = tmp_path / "full"
    dest.mkdir()
    (dest / "mine.txt").write_text("keep")
    assert main(["restore", str(arc), "--into", str(dest)]) == 1
    assert "never overwrites" in capsys.readouterr().err
    assert [p.name for p in dest.iterdir()] == ["mine.txt"]


def test_tampered_member_exits_1_and_leaves_nothing(tmp_path, capsys):
    arc = tmp_path / "b.tar.gz"
    bk.backup(make_workspace(tmp_path / "ws"), arc, warn=lambda m: None)
    bad = tmp_path / "bad.tar.gz"

    def edit(m, data):
        if m.name == "workspace/methods.toml":
            data = b"[evil]\n"
            m.size = len(data)
        return m, data

    rewrite(arc, bad, edit)
    dest = tmp_path / "out"
    assert main(["restore", str(bad), "--into", str(dest)]) == 1
    assert "methods.toml" in capsys.readouterr().err
    assert not dest.exists()
    assert list(tmp_path.glob(".smeltery-restore-*")) == []
    assert main(["restore", str(bad), "--into", str(dest), "--dry-run"]) == 1


def test_malicious_member_refused(tmp_path, capsys):
    arc = tmp_path / "b.tar.gz"
    bk.backup(make_workspace(tmp_path / "ws"), arc, warn=lambda m: None)
    evil = tmp_path / "evil.tar.gz"
    with tarfile.open(arc) as src, tarfile.open(evil, "w:gz") as dst:
        for m in src.getmembers():
            dst.addfile(m, src.extractfile(m))
        x = tarfile.TarInfo("../x")
        x.size = 1
        dst.addfile(x, io.BytesIO(b"x"))
        link = tarfile.TarInfo("workspace/l")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        dst.addfile(link)
    dest = tmp_path / "out"
    assert main(["restore", str(evil), "--into", str(dest)]) == 1
    err = capsys.readouterr().err
    assert "../x" in err and "workspace/l" in err
    assert not dest.exists() and not (tmp_path / "x").exists()


def test_backup_with_open_write_transaction_is_consistent(tmp_path):
    root = make_workspace(tmp_path / "ws")
    path = root / ".smeltery/bench/projects.sqlite"
    writer = sqlite3.connect(path, isolation_level=None)
    writer.execute("BEGIN IMMEDIATE")
    writer.execute("INSERT INTO t VALUES (999, 'uncommitted')")
    try:
        arc = tmp_path / "b.tar.gz"
        bk.backup(root, arc, warn=lambda m: None)
    finally:
        writer.execute("ROLLBACK")
        writer.close()
    dest = tmp_path / "out"
    bk.restore(arc, dest)
    con = sqlite3.connect(dest / ".smeltery/bench/projects.sqlite")
    assert con.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    assert con.execute("SELECT count(*) FROM t WHERE a = 999").fetchone() == (0,)
    assert con.execute("SELECT count(*) FROM t").fetchone() == (50,)


def test_not_an_archive(tmp_path, capsys):
    junk = tmp_path / "junk.tar.gz"
    junk.write_bytes(b"nope")
    assert main(["restore", str(junk), "--into", str(tmp_path / "o")]) == 1
    assert not (tmp_path / "o").exists()


def test_archive_inside_root_restores(tmp_path):
    root = make_workspace(tmp_path / "ws")
    arc = root / "b.tar.gz"
    m = bk.backup(root, arc, warn=lambda m: None)
    assert not any(k.startswith("b.tar.gz") or ".smeltery-backup-" in k for k in m["files"])
    assert bk.restore(arc, tmp_path / "out")
    assert list(root.glob(".smeltery-backup-*")) == []


def test_file_growing_during_backup_still_restores(tmp_path, monkeypatch):
    root = make_workspace(tmp_path / "ws")
    log = root / "run.log"
    log.write_text("line\n")

    def grow(path):
        if path == log:
            with log.open("a") as f:
                f.write("more\n")

    monkeypatch.setattr(bk, "_after_stat", grow)
    arc = tmp_path / "b.tar.gz"
    m = bk.backup(root, arc, warn=lambda m: None)
    assert m["files"]["run.log"]["size"] == 5
    bk.restore(arc, tmp_path / "out")
    assert (tmp_path / "out/run.log").read_text() == "line\n"


def test_file_shrinking_during_backup_fails(tmp_path, monkeypatch):
    root = make_workspace(tmp_path / "ws")
    log = root / "run.log"
    log.write_text("long line\n")
    monkeypatch.setattr(bk, "_after_stat", lambda p: log.write_text("x") if p == log else None)
    arc = tmp_path / "b.tar.gz"
    with pytest.raises(bk.BackupError, match="run.log"):
        bk.backup(root, arc, warn=lambda m: None)
    assert not arc.exists() and not list(tmp_path.glob("*.part")) and not list(tmp_path.glob(".smeltery-backup-*"))


def test_journal_named_plain_file_is_backed_up_but_sqlite_sidecars_are_not(tmp_path):
    root = make_workspace(tmp_path / "ws")
    (root / "run-journal").write_text("keep me")
    (root / ".smeltery/bench/projects.sqlite-wal").write_bytes(b"wal")
    m = bk.backup(root, tmp_path / "b.tar.gz", warn=lambda m: None)
    assert "run-journal" in m["files"]
    assert not any(k.endswith("-wal") for k in m["files"])


def test_existing_out_needs_force(tmp_path, capsys):
    root = make_workspace(tmp_path / "ws")
    arc = tmp_path / "b.tar.gz"
    arc.write_text("precious")
    assert main(["backup", str(root), "--out", str(arc)]) == 1
    assert "--force" in capsys.readouterr().err
    assert arc.read_text() == "precious"
    assert main(["backup", str(root), "--out", str(arc), "--force"]) == 0


def test_non_object_manifest_entry_is_a_problem(tmp_path, capsys):
    arc = tmp_path / "b.tar.gz"
    bk.backup(make_workspace(tmp_path / "ws"), arc, warn=lambda m: None)
    bad = tmp_path / "bad.tar.gz"

    def edit(m, data):
        if m.name == "MANIFEST.json":
            d = json.loads(data)
            d["files"]["methods.toml"] = "oops"
            data = json.dumps(d).encode()
            m.size = len(data)
        return m, data

    rewrite(arc, bad, edit)
    assert main(["restore", str(bad), "--into", str(tmp_path / "o")]) == 1
    assert "methods.toml" in capsys.readouterr().err
    assert not (tmp_path / "o").exists()
