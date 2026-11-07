"""The notebook API: campaigns, append-only entries, artefacts by hash, authorship from Identity."""

import hashlib

from bench.conftest import record_path


def _campaign(c):
    return c.post("/api/campaigns", json={"name": "cdk2"}).json()["id"]


def test_an_entry_records_attached_runs_by_sha256(client_for, run_dir):
    c, _ = client_for(run_dir)
    cid = _campaign(c)
    r = c.post(f"/api/campaigns/{cid}/entries", json={"title": "first pass", "body": "ran it", "runs": [0]})
    assert r.status_code == 201
    assert r.json()["artefacts"] == [hashlib.sha256(record_path(run_dir).read_bytes()).hexdigest()]


def test_there_is_no_route_that_edits_an_entry(client_for):
    c, _ = client_for()
    cid = _campaign(c)
    eid = c.post(f"/api/campaigns/{cid}/entries", json={"title": "t", "body": "b"}).json()["id"]
    assert c.put(f"/api/campaigns/{cid}/entries/{eid}", json={"body": "x"}).status_code in (404, 405)
    assert c.patch(f"/api/campaigns/{cid}/entries/{eid}", json={"body": "x"}).status_code in (404, 405)


def test_a_correction_supersedes_and_the_old_entry_stays_readable(client_for):
    c, _ = client_for()
    cid = _campaign(c)
    old = c.post(f"/api/campaigns/{cid}/entries", json={"title": "t", "body": "b"}).json()["id"]
    new = c.post(f"/api/campaigns/{cid}/entries", json={"title": "fix", "body": "b2", "supersedes": old}).json()
    assert new["supersedes"] == old
    got = c.get(f"/api/campaigns/{cid}/entries/{old}").json()
    assert got["superseded_by"] == new["id"] and got["body"] == "b"


def test_superseding_an_unknown_entry_is_400(client_for):
    c, _ = client_for()
    cid = _campaign(c)
    assert c.post(f"/api/campaigns/{cid}/entries", json={"title": "t", "supersedes": "ghost"}).status_code == 400


def test_the_author_comes_from_identity(client_for):
    c, _ = client_for(identity="alice")
    cid = _campaign(c)
    r = c.post(f"/api/campaigns/{cid}/entries", json={"title": "t", "body": "b", "author": "forged"})
    assert r.json()["author"] == "alice"


def test_an_unknown_campaign_is_404(client_for):
    c, _ = client_for()
    assert c.get("/api/campaigns/nope/entries").status_code == 404


def test_a_campaign_reports_its_entry_and_run_counts(client_for, run_dir):
    c, _ = client_for(run_dir)
    cid = _campaign(c)
    empty = c.get(f"/api/campaigns/{cid}").json()
    assert (empty["n_entries"], empty["n_runs"], empty["updated_at"]) == (0, 0, empty["created_at"])
    e = c.post(f"/api/campaigns/{cid}/entries", json={"title": "t", "runs": [0]}).json()
    got = next(x for x in c.get("/api/campaigns").json() if x["id"] == cid)
    assert (got["n_entries"], got["n_runs"], got["updated_at"]) == (1, 1, e["created_at"])
