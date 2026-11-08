"""The report surface's context: the floor sentence, /api/limits, and the snapshot's comments and decisions."""

from smeltery.gui import limits
from smeltery.gui.limits import load_limits

HOSTILE = "<script>alert(1)</script> & co"


def test_limits_endpoint_serves_the_doc_and_null_without_it(client_for, run_dir, monkeypatch, tmp_path):
    c, _ = client_for(run_dir)
    body = c.get("/api/limits").json()
    assert body["measured"][0]["id"] == "L1" and body["unmeasured"]
    monkeypatch.setattr(limits, "LIMITS_DOC", tmp_path / "absent.md")
    monkeypatch.setattr("smeltery.bench.api.methods.load_limits", lambda: load_limits(tmp_path / "absent.md"))
    r = c.get("/api/limits")
    assert r.status_code == 200 and r.json() is None


def test_run_detail_carries_the_floor_statement(client_for, run_dir):
    c, _ = client_for(run_dir)
    d = c.get("/api/runs/0").json()
    assert (
        d["floor_statement"].startswith(f"The floor is {d['floor']:g} {d['unit']}")
        and d["floor_source"] in d["floor_statement"]
    )


def test_snapshot_escapes_comments_and_shows_decisions(client_for, run_dir):
    c, _ = client_for(run_dir, identity="<b>eve</b>")
    assert c.post("/api/runs/0/comments", json={"anchor": "run", "body": HOSTILE}).status_code == 201
    r = c.post(
        "/api/runs/0/decisions",
        json={"candidate": "4-F", "status": "reject", "reason": "synthesis", "note": "<i>no</i>"},
    )
    assert r.status_code in (200, 201), r.text
    snap = c.post("/api/runs/0/snapshot").json()
    html = open(snap["path"], encoding="utf-8").read()
    assert HOSTILE not in html and "&lt;script&gt;alert(1)&lt;/script&gt; &amp; co" in html
    assert "<b>eve</b>" not in html and "&lt;b&gt;eve&lt;/b&gt;" in html
    assert "<h2>Decisions</h2>" in html and "<td>4-F</td>" in html and "&lt;i&gt;no&lt;/i&gt;" in html
    assert "<h2>Comments</h2>" in html and "The floor is 0 kcal/mol" in html


def test_snapshot_with_no_comments_or_decisions_says_so(client_for, run_dir):
    c, _ = client_for(run_dir)
    html = open(c.post("/api/runs/0/snapshot").json()["path"], encoding="utf-8").read()
    assert "No comments on this run." in html and "No decisions recorded." in html
