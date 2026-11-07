"""The report surface's API: anchored comments, proposals that start no work, the frozen snapshot, and compare."""

import hashlib
import re
from pathlib import Path

from bench.fakes import FakeTaskQueue
from smeltery.bench.api.runs import REPORT_EXTERNAL


def test_comments_anchor_to_run_candidate_and_pose(client_for, run_dir):
    c, _ = client_for(run_dir, identity="bench-chemist")
    for anchor in ("run", "candidate:4-F", "pose:4-F:2"):
        r = c.post("/api/runs/0/comments", json={"anchor": anchor, "body": f"note on {anchor}"})
        assert r.status_code == 201 and r.json()["author"] == "bench-chemist", anchor
    got = c.get("/api/runs/0/comments").json()
    assert [x["anchor"] for x in got] == ["run", "candidate:4-F", "pose:4-F:2"]


def test_a_comment_author_in_the_body_is_ignored(client_for, run_dir):
    c, _ = client_for(run_dir, identity="alice")
    r = c.post("/api/runs/0/comments", json={"anchor": "run", "body": "hi", "author": "forged"})
    assert r.json()["author"] == "alice"


def test_bad_anchors_and_empty_comments_are_400(client_for, run_dir):
    c, _ = client_for(run_dir)
    assert c.post("/api/runs/0/comments", json={"anchor": "everything", "body": "x"}).status_code == 400
    assert c.post("/api/runs/0/comments", json={"anchor": "run", "body": "  "}).status_code == 400


def test_a_proposal_is_stored_for_review_and_submits_nothing(client_for, run_dir):
    q = FakeTaskQueue()
    c, _ = client_for(run_dir, queue=q, identity="bench-chemist")
    r = c.post("/api/runs/0/proposals", json={"smiles": "Cc1ccccc1", "reason": "synthesis", "note": "try the 3-F"})
    assert r.status_code == 201 and r.json()["status"] == "pending"
    queue = c.get("/api/proposals").json()
    assert [(p["smiles"], p["author"]) for p in queue] == [("Cc1ccccc1", "bench-chemist")]
    assert q.submitted == []


def test_a_bad_proposal_is_400_and_not_stored(client_for, run_dir):
    c, _ = client_for(run_dir)
    assert c.post("/api/runs/0/proposals", json={"smiles": "C1CC", "reason": "synthesis"}).status_code == 400
    assert c.post("/api/runs/0/proposals", json={"smiles": "CCO", "reason": "vibes"}).status_code == 400
    assert c.get("/api/proposals").json() == []


def test_the_snapshot_is_written_unmodified_beside_the_run(client_for, run_dir):
    """The frozen report's only external references are its pinned, integrity-hashed libraries, and every mean the
    served API reports appears in it. Data source: /api/runs/0/ddE, and the file on disk."""
    c, _ = client_for(run_dir)
    snap = c.post("/api/runs/0/snapshot").json()
    p = Path(snap["path"])
    data = p.read_bytes()
    assert p.parent == run_dir and snap["sha256"] == hashlib.sha256(data).hexdigest()
    urls = set(re.findall(r"https?://[^\s\"'<>)]+", data.decode()))
    assert urls and urls <= set(REPORT_EXTERNAL), urls - set(REPORT_EXTERNAL)
    assert 'integrity="sha' in data.decode() or "integrity" in data.decode()
    for row in c.get("/api/runs/0/ddE").json()["rows"]:
        assert f"{row['mean']:.2f}" in data.decode(), row["candidate"]


def test_compare_differences_two_runs_and_refuses_one_run_in_both_arms(client_for, tmp_path):
    import json

    from gui_fixture import make_run_dir

    make_run_dir(tmp_path / "a")
    b = make_run_dir(tmp_path / "b")
    rec = next(b.glob("run-*.json"))
    data = json.loads(rec.read_text())
    for m in data["results"]["paired_ddE"].values():
        m["mean"] += 1.0  # data source: arm b is worse by exactly 1.0 kcal/mol on every analogue
    rec.write_text(json.dumps(data))
    c, _ = client_for(tmp_path)
    r = c.get("/api/compare", params={"a": 0, "b": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["n_paired"] == 1 and body["summary"]["median_per_key_diff"] == -1.0
    assert body["figure"]["data"] and body["caption"]
    (row,) = body["rows"]
    assert row["diff"] == -1.0 and not row["unpaired"] and body["unit"]
    same = c.get("/api/compare", params={"a": 0, "b": 0})
    assert same.status_code == 400 and "both arms" in same.json()["detail"]


def test_two_runs_of_one_campaign_are_named_apart_in_the_comparison(client_for, tmp_path):
    """The usual comparison is two runs of one campaign; even with the same input digest, the arms must not read the
    same, or the caption says a run favours itself."""
    from gui_fixture import make_run_dir

    make_run_dir(tmp_path / "a")
    make_run_dir(tmp_path / "b")  # data source: the same fixture twice, so campaign and digest are both equal
    c, _ = client_for(tmp_path)
    body = c.get("/api/compare", params={"a": 0, "b": 1}).json()
    title = body["figure"]["layout"]["title"]["text"]
    assert "A (" in title and "B (" in title, title
    assert "A (" in body["caption"] or "B (" in body["caption"], body["caption"]


def test_a_key_one_arm_did_not_run_is_a_row_not_a_crash(client_for, tmp_path):
    import json

    from gui_fixture import make_run_dir

    make_run_dir(tmp_path / "a")
    b = make_run_dir(tmp_path / "b")
    rec = next(b.glob("run-*.json"))
    data = json.loads(rec.read_text())
    data["results"]["cut"]["groups"] = []  # data source: arm b ranked no analogues
    rec.write_text(json.dumps(data))
    c, _ = client_for(tmp_path)
    r = c.get("/api/compare", params={"a": 0, "b": 1})
    assert r.status_code == 200, r.text
    (row,) = r.json()["rows"]
    assert row["unpaired"] and row["b"] is None and row["n_b"] == 0 and row["a"] is not None
