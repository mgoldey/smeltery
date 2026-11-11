"""B13: the task list and the log tail are bounded however much history there is."""

from __future__ import annotations

import json
import os
import sqlite3

from smeltery.bench.adapters import task_queue as tq
from smeltery.bench.adapters.task_queue import SubprocessTaskQueue, read_tail


def _insert(db, n, status, *, prefix, start=0, log_path=""):
    con = sqlite3.connect(db)
    with con:
        for i in range(n):
            con.execute(
                "INSERT INTO tasks (id,name,command,cwd,status,created_at,log_path,note) VALUES (?,?,?,?,?,?,?,?)",
                (
                    f"{prefix}{i}",
                    "t",
                    json.dumps(["true"]),
                    "",
                    status,
                    f"2026-01-01T00:{(start + i) // 60:02d}:{(start + i) % 60:02d}+00:00",
                    log_path,
                    "",
                ),
            )
    con.close()


def test_the_list_is_bounded_and_newest_first(tmp_path):
    q = SubprocessTaskQueue(tmp_path)
    _insert(q.db, 500, "succeeded", prefix="d")
    got = q.list(limit=25)
    assert [t.id for t in got] == [f"d{i}" for i in range(499, 474, -1)]
    assert len(q.list()) == tq.DEFAULT_LIST_LIMIT
    assert len(q.list(limit=None)) == 500


def test_live_tasks_are_never_pushed_off_by_history(tmp_path):
    q = SubprocessTaskQueue(tmp_path)
    _insert(q.db, 1, "queued", prefix="old-live")  # the OLDEST row, and still live
    _insert(q.db, 300, "failed", prefix="d", start=10)
    got = q.list(limit=10)
    assert len(got) == 10 and "old-live0" in [t.id for t in got]
    created = [t.created_at for t in got]
    assert created == sorted(created, reverse=True)


def test_list_with_a_status_filter_is_bounded_too(tmp_path):
    q = SubprocessTaskQueue(tmp_path)
    _insert(q.db, 40, "failed", prefix="f")
    assert len(q.list(status="failed", limit=7)) == 7


def test_tail_of_a_large_log_reads_a_bounded_number_of_bytes(tmp_path, monkeypatch):
    log = tmp_path / "big.log"
    with log.open("w") as f:
        for i in range(500_000):  # ~ 8 MB, enough that reading it all is unmistakable
            f.write(f"line {i:07d} padding padding\n")
    size = log.stat().st_size
    assert size > 8_000_000
    read = []
    real = os.pread

    def spy(fd, n, offset):
        read.append((n, offset))
        return real(fd, n, offset)

    monkeypatch.setattr(tq.os, "pread", spy)
    out = read_tail(log, 200).split("\n")
    assert out == [f"line {i:07d} padding padding" for i in range(499_800, 500_000)]
    assert sum(n for n, _ in read) <= 4 * tq._TAIL_BLOCK
    assert min(off for _, off in read) > size - 5 * tq._TAIL_BLOCK  # only ever the end of the file


def test_tail_edge_cases(tmp_path):
    p = tmp_path / "x.log"
    p.write_bytes(b"")
    assert read_tail(p, 5) == ""
    p.write_bytes(b"a\nb\nc")  # no trailing newline
    assert read_tail(p, 2) == "b\nc"
    assert read_tail(p, 50) == "a\nb\nc"
    assert read_tail(p, 0) == ""
    p.write_bytes("é\n".encode() * 100_000)  # multi-byte characters across block boundaries
    assert read_tail(p, 3) == "é\né\né"


def test_a_line_longer_than_the_byte_cap_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(tq, "_TAIL_BLOCK", 1024)
    monkeypatch.setattr(tq, "_TAIL_MAX_BYTES", 8192)
    p = tmp_path / "bar.log"
    p.write_bytes(b"first\n" + b"x" * 1_000_000 + b"\nlast\n")
    out = read_tail(p, 5)
    assert out.endswith("last") and len(out) < 10_000


def test_tail_log_through_the_queue(tmp_path):
    q = SubprocessTaskQueue(tmp_path)
    log = tmp_path / "t.log"
    log.write_text("\n".join(f"l{i}" for i in range(1000)) + "\n")
    _insert(q.db, 1, "succeeded", prefix="t", log_path=str(log))
    assert q.tail_log("t0", lines=3) == "l997\nl998\nl999"
    log.unlink()
    assert q.tail_log("t0") == ""
