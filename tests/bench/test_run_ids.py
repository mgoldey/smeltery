"""B04: a run is addressed by a stable id, discovery is cached, and the walk skips the big uninteresting trees."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from gui_fixture import make_run_dir

from bench.fakes import FakeIdentity, FakeRunStore, FakeTaskQueue, FixedClock
from smeltery.bench.adapters import run_store as rs
from smeltery.bench.adapters.run_store import FilesystemRunStore
from smeltery.bench.app import create_app


def client(root):
    return TestClient(
        create_app(
            run_store=FakeRunStore(root),
            task_queue=FakeTaskQueue(),
            identity=FakeIdentity("mg"),
            clock=FixedClock(),
        )
    )


def record_of(out_dir):
    (p,) = out_dir.glob("run-*.json")
    return p


def add_earlier_run(root, source_record):
    """A second, valid run in a directory that sorts BEFORE the first: `aaa/` beats any later name."""
    out = root / "aaa"
    out.mkdir(parents=True)
    rec = json.loads(source_record.read_text())
    (out / f"run-{rec['input_digest'][:12]}.json").write_text(json.dumps(rec))
    return out


# -------------------------------------------------------------------------------- the id itself


def test_an_id_is_the_digest_prefix_and_a_path_hash(tmp_path):
    out = make_run_dir(tmp_path / "root")
    (row,) = FilesystemRunStore(tmp_path / "root").list_runs()
    digest = json.loads(record_of(out).read_text())["input_digest"]
    head, _, tail = row["id"].partition("-")
    assert head == digest[:12]
    assert len(tail) == 6 and all(c in "0123456789abcdef" for c in tail)


def test_two_copies_of_one_digest_in_different_directories_get_different_ids(tmp_path):
    root = tmp_path / "root"
    out = make_run_dir(root)
    add_earlier_run(root, record_of(out))
    ids = [r["id"] for r in FilesystemRunStore(root).list_runs()]
    assert len(ids) == 2
    assert len({i.split("-")[0] for i in ids}) == 1, "same digest"
    assert len(set(ids)) == 2, "different ids"


def test_a_record_with_no_readable_digest_is_still_addressable(tmp_path):
    root = tmp_path / "root"
    make_run_dir(root)
    (root / "run-000000000bad.json").write_text("{not json")
    rows = FilesystemRunStore(root).list_runs()
    bad = [r for r in rows if r.get("error")]
    assert len(bad) == 1 and bad[0]["id"].startswith("unknown-")


def test_an_id_is_never_all_digits_so_it_cannot_look_like_a_position(tmp_path):
    out = make_run_dir(tmp_path / "root")
    (row,) = FilesystemRunStore(tmp_path / "root").list_runs()
    assert not row["id"].isdigit() and out


# -------------------------------- THE regression: a new run that sorts earlier must not move an existing one


def test_a_run_added_that_sorts_earlier_leaves_the_original_id_resolving_to_the_same_record(tmp_path):
    root = tmp_path / "root"
    out = make_run_dir(root)
    c = client(root)
    listed = c.get("/api/runs").json()
    assert len(listed) == 1
    rid, path = listed[0]["id"], listed[0]["path"]

    add_earlier_run(root, record_of(out))

    after = c.get("/api/runs").json()
    assert len(after) == 2
    assert [r["id"] for r in after].count(rid) == 1
    # The id still names the SAME record file -- while its position has moved from 0 to 1.
    assert c.get(f"/api/runs/{rid}").json()["path"] == path
    assert [r["id"] for r in after].index(rid) == 1, "the new run really does sort earlier"
    assert c.get("/api/runs/0").json()["path"] != path, "position 0 is now the other run"


def test_removing_a_run_that_sorted_earlier_also_leaves_the_id_alone(tmp_path):
    root = tmp_path / "root"
    out = make_run_dir(root)
    earlier = add_earlier_run(root, record_of(out))
    c = client(root)
    rid = next(r["id"] for r in c.get("/api/runs").json() if r["path"] == str(record_of(out)))
    record_of(earlier).unlink()
    assert c.get(f"/api/runs/{rid}").json()["path"] == str(record_of(out))


def test_an_in_flight_decision_lands_on_the_run_its_id_names(tmp_path):
    root = tmp_path / "root"
    out = make_run_dir(root)
    c = client(root)
    rid = c.get("/api/runs").json()[0]["id"]
    add_earlier_run(root, record_of(out))  # the race B04 fixes: a run appears between read and write
    posted = c.post(f"/api/runs/{rid}/decisions", json={"candidate": "4-F", "status": "keep", "reason": "cut_survivor"})
    assert posted.status_code == 200, posted.text
    got = c.get(f"/api/runs/{rid}/decisions").json()
    assert [d["candidate"] for d in got] == ["4-F"]
    assert (out / f"decisions-{rid.split('-')[0]}.json").is_file()


# -------------------------------------------------------------------------------- old integer links


def test_an_old_integer_link_still_resolves_and_says_it_is_deprecated(tmp_path):
    root = tmp_path / "root"
    make_run_dir(root)
    c = client(root)
    rid = c.get("/api/runs").json()[0]["id"]
    r = c.get("/api/runs/0")
    assert r.status_code == 200 and r.json()["id"] == rid
    assert r.headers["Deprecation"] == "true"
    assert r.headers["Link"] == f'</api/runs/{rid}>; rel="canonical"'


def test_the_deprecation_link_keeps_the_rest_of_the_path(tmp_path):
    root = tmp_path / "root"
    make_run_dir(root)
    c = client(root)
    rid = c.get("/api/runs").json()[0]["id"]
    r = c.get("/api/runs/0/verdict")
    assert r.status_code == 200
    assert r.headers["Link"] == f'</api/runs/{rid}/verdict>; rel="canonical"'


def test_a_stable_id_carries_no_deprecation_header(tmp_path):
    root = tmp_path / "root"
    make_run_dir(root)
    c = client(root)
    rid = c.get("/api/runs").json()[0]["id"]
    r = c.get(f"/api/runs/{rid}")
    assert r.status_code == 200 and "Deprecation" not in r.headers


@pytest.mark.parametrize("ref", ["7", "deadbeefcafe-abc123", "not-an-id"])
def test_an_unknown_reference_of_either_form_is_a_404_saying_what_is_known(tmp_path, ref):
    root = tmp_path / "root"
    make_run_dir(root)
    r = client(root).get(f"/api/runs/{ref}")
    assert r.status_code == 404
    assert "1 known" in json.dumps(r.json())


# -------------------------------------------------------------------------------- caching


def test_an_unchanged_record_is_parsed_once_across_repeated_requests(tmp_path, monkeypatch):
    root = tmp_path / "root"
    make_run_dir(root)
    calls: list[str] = []
    real = rs.load_run

    def counted(path):
        calls.append(str(path))
        return real(path)

    monkeypatch.setattr(rs, "load_run", counted)
    store = FilesystemRunStore(root)
    for _ in range(5):
        store.list_runs()
    assert len(calls) == 1, calls


def test_a_rewritten_record_is_reparsed(tmp_path, monkeypatch):
    root = tmp_path / "root"
    out = make_run_dir(root)
    calls: list[str] = []
    real = rs.load_run
    monkeypatch.setattr(rs, "load_run", lambda p: (calls.append(str(p)), real(p))[1])
    store = FilesystemRunStore(root)
    store.list_runs()
    p = record_of(out)
    rec = json.loads(p.read_text())
    rec["campaign"] = "renamed"
    p.write_text(json.dumps(rec) + " ")  # a different size, so the stat key changes whatever the clock did
    assert store.list_runs()[0]["campaign"] == "renamed"
    assert len(calls) == 2, calls


def test_the_api_reads_each_record_file_once_per_request_not_twice(tmp_path, monkeypatch):
    root = tmp_path / "root"
    make_run_dir(root)
    c = client(root)
    rid = c.get("/api/runs").json()[0]["id"]
    reads: list[str] = []
    real = type(root).read_text

    def counted(self, *a, **k):
        reads.append(str(self))
        return real(self, *a, **k)

    monkeypatch.setattr(type(root), "read_text", counted)
    assert c.get(f"/api/runs/{rid}/verdict").status_code == 200
    assert [p for p in reads if "run-" in p] == [], "the record came from the cache, not the disk"


# -------------------------------------------------------------------------------- the walk


def test_the_walk_does_not_descend_into_venv_or_node_modules(tmp_path):
    root = tmp_path / "root"
    make_run_dir(root)
    for skip in sorted(rs.SKIP_DIRS):
        buried = root / skip / "deep"
        buried.mkdir(parents=True)
        (buried / "run-ffffffffffff.json").write_text("{}")
    store = FilesystemRunStore(root)
    found = store.find_runs()
    assert len(found) == 1, found
    assert not any(part in rs.SKIP_DIRS for p in found for part in p.parts)
    assert len(store.list_runs()) == 1


def test_a_skipped_directory_is_not_even_opened(tmp_path, monkeypatch):
    root = tmp_path / "root"
    make_run_dir(root)
    (root / "node_modules" / "pkg").mkdir(parents=True)
    seen: list[str] = []
    real = type(root).iterdir
    monkeypatch.setattr(type(root), "iterdir", lambda self: (seen.append(str(self)), real(self))[1])
    FilesystemRunStore(root).find_runs()
    assert not any("node_modules" in s for s in seen), seen


def test_a_deleted_record_leaves_nothing_in_the_cache(tmp_path):
    """A long-running server lists ROOT on every request; a cache that only ever grew would be a slow leak."""
    root = tmp_path / "root"
    out = make_run_dir(root)
    store = FilesystemRunStore(root)
    store.list_runs()
    assert len(store._cache) == 1
    record_of(out).unlink()
    assert store.list_runs() == []
    assert store._cache == {} and store._records == {}


def test_no_sqlite_index_is_created(tmp_path):
    root = tmp_path / "root"
    make_run_dir(root)
    store = FilesystemRunStore(root)
    store.list_runs()
    assert [p.name for p in root.rglob("*.sqlite")] == []
    assert not hasattr(store, "index_run"), "the dead sqlite index is gone, not merely unused"
