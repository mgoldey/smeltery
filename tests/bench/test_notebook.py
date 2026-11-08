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


# ------------------------------------------------------------------------------------------- file attachments

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20


def _upload(c, name, data):
    return c.post("/api/notebook/artifacts", params={"filename": name}, content=data)


def test_a_file_attaches_round_trips_and_is_not_counted_as_a_run(client_for, run_dir):
    c, _ = client_for(run_dir)
    cid = _campaign(c)
    ref = _upload(c, "fig.png", PNG)
    assert ref.status_code == 201
    ref = ref.json()
    assert ref["digest"] == hashlib.sha256(PNG).hexdigest()
    assert (ref["media_type"], ref["kind"]) == ("image/png", "file")
    e = c.post(f"/api/campaigns/{cid}/entries", json={"title": "t", "runs": [0], "files": [ref]}).json()
    assert e["files"] == [ref] and len(e["artefacts"]) == 1 and ref["digest"] not in e["artefacts"]
    assert c.get(f"/api/campaigns/{cid}/entries/{e['id']}").json()["files"] == [ref]
    assert c.get(f"/api/campaigns/{cid}").json()["n_runs"] == 1
    got = c.get(f"/api/notebook/artifacts/{ref['digest']}", params={"name": "fig.png"})
    assert got.content == PNG and got.headers["content-type"] == "image/png"
    assert got.headers["content-disposition"].startswith("inline")


def test_svg_and_html_are_only_ever_downloads(client_for):
    c, _ = client_for()
    for name, ctype in (("x.svg", "image/svg+xml"), ("x.html", "application/octet-stream")):
        ref = _upload(c, name, b"<script>alert(1)</script>").json()
        got = c.get(f"/api/notebook/artifacts/{ref['digest']}", params={"name": name})
        assert got.headers["content-disposition"].startswith("attachment")
        assert got.headers["content-type"].split(";")[0] == ctype
        assert got.headers["x-content-type-options"] == "nosniff"


def test_a_corrupt_stored_file_is_a_clear_422_not_a_500(client_for):
    c, _ = client_for()
    ref = _upload(c, "t.csv", b"a,b\n1,2\n").json()
    c.app.state.artifact_store.path_for(ref["digest"]).write_bytes(b"tampered")
    got = c.get(f"/api/notebook/artifacts/{ref['digest']}", params={"name": "t.csv"})
    assert got.status_code == 422 and "sha256" in got.json()["detail"]
    assert c.get(f"/api/notebook/artifacts/{'0' * 64}").status_code == 404
    assert c.get("/api/notebook/artifacts/not-a-digest").status_code == 422


def test_an_entry_cannot_reference_a_file_that_was_never_uploaded(client_for):
    c, _ = client_for()
    cid = _campaign(c)
    ghost = {"digest": "1" * 64, "filename": "a.png", "media_type": "image/png", "kind": "file"}
    assert c.post(f"/api/campaigns/{cid}/entries", json={"title": "t", "files": [ghost]}).status_code == 400
    assert (
        c.post(f"/api/campaigns/{cid}/entries", json={"title": "t", "files": [ghost | {"digest": "x"}]}).status_code
        == 400
    )


def test_an_empty_upload_is_400_and_a_client_media_type_is_ignored(client_for):
    c, _ = client_for()
    assert _upload(c, "a.png", b"").status_code == 400
    ref = c.post(
        "/api/notebook/artifacts",
        params={"filename": "../../a.html"},
        content=b"x",
        headers={"content-type": "image/png"},
    ).json()
    assert ref["filename"] == "a.html" and ref["media_type"] == "application/octet-stream"


def test_an_old_row_without_the_files_column_still_reads_and_counts_as_runs(client_for, run_dir):
    import sqlite3

    c, _ = client_for(run_dir)
    cid = _campaign(c)
    store = c.app.state.project_store.db_path
    d = hashlib.sha256(b"old").hexdigest()
    con = sqlite3.connect(store)
    con.execute("UPDATE campaigns SET id=id")
    con.execute(
        "INSERT INTO entries (id,campaign_id,title,body,author,created_at,artefacts,supersedes,files) "
        "VALUES ('old1',?,'old','b','mg','2020-01-01T00:00:00Z',?,NULL,NULL)",
        (cid, f'["{d}"]'),
    )
    con.commit()
    con.close()
    e = c.get(f"/api/campaigns/{cid}/entries/old1").json()
    assert e["artefacts"] == [d] and e["files"] == []
    assert c.get(f"/api/campaigns/{cid}").json()["n_runs"] == 1


def test_a_database_made_before_files_existed_is_migrated_in_place(tmp_path):
    import sqlite3

    from smeltery.bench.adapters.project_store import SqliteProjectStore

    db = tmp_path / "p.sqlite"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE entries (id TEXT PRIMARY KEY, campaign_id TEXT, title TEXT, body TEXT, author TEXT, "
        "created_at TEXT, artefacts TEXT, supersedes TEXT)"
    )
    con.execute("INSERT INTO entries VALUES ('o','c','t','b','a','2020-01-01T00:00:00Z',?,NULL)", (f'["{"a" * 64}"]',))
    con.commit()
    con.close()
    (e,) = SqliteProjectStore(db).entries("c")
    assert e.artefacts == ("a" * 64,) and e.files == ()
