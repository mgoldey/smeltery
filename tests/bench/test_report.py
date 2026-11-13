"""The report surface's API: anchored comments, proposals that start no work, the frozen snapshot, and compare."""

import hashlib
import json
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


def test_the_report_downloads_as_the_same_bytes_the_snapshot_archives(client_for, run_dir):
    """One builder behind both ways out, so the file a chemist saves is the file the notebook archives.

    Data source: the download's body and the archived file on disk.
    """
    c, _ = client_for(run_dir)
    got = c.get("/api/runs/0/report.html")
    assert got.status_code == 200
    assert got.headers["content-type"].startswith("text/html")
    # It is offered as a file, under a name that names the run it came from, not "download.html".
    digest = c.get("/api/runs/0").json()["digest"][:12]
    assert got.headers["content-disposition"] == f'attachment; filename="report-{digest}.html"'
    archived = Path(c.post("/api/runs/0/snapshot").json()["path"]).read_bytes()
    assert got.content == archived


def _record_config(run_dir, text: str, name: str = "config.toml") -> Path:
    """Put a config beside the run and point the record at it, as a real `smeltery run` does."""
    import json

    path = Path(run_dir) / name
    path.write_text(text)
    (rec_path,) = Path(run_dir).glob("run-*.json")
    rec = json.loads(rec_path.read_text())
    rec["results"]["config"] = {"file": name, "sha256": hashlib.sha256(text.encode()).hexdigest()}
    rec_path.write_text(json.dumps(rec))
    return path


CONFIG = """# why this round exists: the comments are the documentation
[cut]
keep = 1
"""


def test_downloading_the_report_writes_nothing(client_for, run_dir):
    """The download renders in memory. It is a GET, and `--read-only` refuses by method, so a GET that wrote to
    the run directory would be a write nobody refuses. Data source: the run directory before and after."""
    c, _ = client_for(run_dir)
    before = sorted(p.name for p in Path(run_dir).glob("report-*.html"))
    assert c.get("/api/runs/0/report.html").status_code == 200
    assert sorted(p.name for p in Path(run_dir).glob("report-*.html")) == before
    # ...while the archiving route is the one that leaves the file behind.
    assert Path(c.post("/api/runs/0/snapshot").json()["path"]).is_file()


def test_the_config_endpoint_serves_the_file_the_run_read_with_its_comments(client_for, run_dir):
    """Provenance shows the config as text. The comments are the project's documentation, so a rebuild from the
    record would not do: this must be the bytes on disk. Data source: the file the record names."""
    _record_config(run_dir, CONFIG)
    c, _ = client_for(run_dir)
    cfg = c.get("/api/runs/0/config").json()
    assert cfg["file"] == "config.toml" and cfg["changed"] is False and cfg["reason"] is None
    assert cfg["text"] == CONFIG
    assert "# why this round exists" in cfg["text"], "the comments must survive; they are the documentation"
    assert cfg["sha256"] == hashlib.sha256(CONFIG.encode()).hexdigest()


def test_a_run_with_no_recorded_config_says_so_rather_than_rebuilding_one(client_for, run_dir):
    """A config rebuilt from the record would read as provenance while not being what ran. Better to say nothing."""
    c, _ = client_for(run_dir)  # the contract fixture records no config, as a run that predates the field
    cfg = c.get("/api/runs/0/config").json()
    assert cfg["text"] is None and cfg["file"] is None
    assert "does not name a config" in cfg["reason"]


def test_a_config_the_record_names_but_that_is_gone_says_which_file_is_missing(client_for, run_dir):
    c, _ = client_for(run_dir)
    _record_config(run_dir, CONFIG).unlink()
    gone = c.get("/api/runs/0/config").json()
    assert gone["text"] is None and gone["file"] == "config.toml"
    assert "no longer beside the run" in gone["reason"]


def test_a_config_name_that_points_outside_the_run_directory_is_refused(client_for, run_dir, tmp_path):
    """The config's name comes out of the record, a file on disk. It never serves anything outside the run."""
    secret = Path(run_dir).parent / "secret.toml"
    secret.write_text("# not part of this run\n")
    _record_config(run_dir, CONFIG)
    (rec_path,) = Path(run_dir).glob("run-*.json")
    rec = json.loads(rec_path.read_text())
    rec["results"]["config"] = {"file": "../secret.toml", "sha256": "x"}
    rec_path.write_text(json.dumps(rec))
    c, _ = client_for(run_dir)
    got = c.get("/api/runs/0/config").json()
    assert got["text"] is None and "outside the run directory" in got["reason"]


def test_a_config_edited_since_the_run_is_shown_but_flagged(client_for, run_dir):
    """The file on disk is what is shown, but a run is only reproducible from the bytes that ran."""
    path = _record_config(run_dir, CONFIG)
    c, _ = client_for(run_dir)
    path.write_text(CONFIG + "# edited after the run\n")
    after = c.get("/api/runs/0/config").json()
    assert after["changed"] is True and "changed" in after["reason"]
    assert after["text"].endswith("# edited after the run\n")


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
    assert same.status_code == 400 and "both arms" in same.json()["reason"]


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


def test_candidates_missing_from_one_run_are_rows_when_parents_match(client_for, tmp_path):
    import json

    from gui_fixture import make_run_dir

    make_run_dir(tmp_path / "a")
    b = make_run_dir(tmp_path / "b")
    rec = next(b.glob("run-*.json"))
    data = json.loads(rec.read_text())
    res = data["results"]
    del res["paired_ddE"]["4-Br"]  # data source: run b never ran 4-Br
    res["cut"]["groups"] = [[n for n in g if n != "4-Br"] for g in res["cut"]["groups"]]
    res["cut"]["groups"] = [g for g in res["cut"]["groups"] if g]
    res["cut"]["survivors"] = (
        None if res["cut"]["survivors"] is None else [n for n in res["cut"]["survivors"] if n != "4-Br"]
    )
    rec.write_text(json.dumps(data))
    c, _ = client_for(tmp_path)
    body = c.get("/api/compare", params={"a": 0, "b": 1}).json()
    assert "does not bound systematic differences" in body["candidates_note"]
    rows = {r["candidate"]: r for r in body["candidates"]}
    assert rows["4-Br"]["ddE_b"] is None and rows["4-Br"]["ddE_a"] is not None and rows["4-Br"]["resolved"] is None
    assert rows["4-F"]["diff_sem"] is not None
