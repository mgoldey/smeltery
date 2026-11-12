"""WS-011 rename/clone and WS-014 bundle import: lifecycle of a workspace pipeline."""

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

from smeltery.bench.api import pipelines as api
from smeltery.bench.use_cases import bundle_import
from smeltery.tasks import Task

PDB = "ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00  0.00           N\n"
BLANK = {"campaign": "c", "parent": "a", "candidates": {"a": "CCO", "b": "CCN"}}


def _made(c, name="p"):
    assert c.post("/api/pipelines", json={"name": name, "blank": BLANK}).status_code == 201
    return name


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _bundle(files: dict[str, bytes], top="evil", extra=None, manifest=None) -> bytes:
    """A zip as the exporter writes it, with `files` listed in the manifest; `extra` entries are added unlisted."""
    buf = io.BytesIO()
    listed = {rel: {"sha256": _sha(b), "size": len(b)} for rel, b in files.items()}
    man = manifest if manifest is not None else {"pipeline": top, "smeltery": "t", "files": listed, "missing": []}
    with zipfile.ZipFile(buf, "w") as z:
        for rel, b in files.items():
            z.writestr(f"{top}/{rel}", b)
        for name, b in (extra or {}).items():
            z.writestr(name, b)
        z.writestr(f"{top}/manifest.json", json.dumps(man))
    return buf.getvalue()


def _import(c, data, **q):
    return c.post("/api/pipelines/import", content=data, params=q)


# ------------------------------------------------------------------------------------------------ rename / clone


def test_rename_moves_the_pipeline_with_its_files(client_for):
    c, _ = client_for()
    _made(c)
    c.put("/api/pipelines/p/files/r.pdb", content=PDB.encode())
    r = c.post("/api/pipelines/p/rename", json={"new_name": "q"})
    assert r.status_code == 200 and r.json()["name"] == "q"
    assert c.get("/api/pipelines").json() == ["q"]
    assert c.get("/api/pipelines/p").status_code == 404
    assert [f["name"] for f in c.get("/api/pipelines/q/files").json()] == ["r.pdb"]


def test_clone_copies_and_keeps_the_source(client_for):
    c, _ = client_for()
    _made(c)
    c.put("/api/pipelines/p/files/r.pdb", content=PDB.encode())
    r = c.post("/api/pipelines/p/clone", json={"new_name": "q"})
    assert r.status_code == 201, r.text
    assert c.get("/api/pipelines").json() == ["p", "q"]
    assert c.get("/api/pipelines/q").json()["toml"] == c.get("/api/pipelines/p").json()["toml"]
    assert [f["name"] for f in c.get("/api/pipelines/q/files").json()] == ["r.pdb"]


@pytest.mark.parametrize("op", ["rename", "clone"])
def test_rename_and_clone_conflicts_and_bad_names(client_for, op):
    c, _ = client_for()
    _made(c, "p")
    _made(c, "q")
    assert c.post(f"/api/pipelines/p/{op}", json={"new_name": "q"}).status_code == 409
    assert c.post(f"/api/pipelines/p/{op}", json={"new_name": "../x"}).status_code == 400
    assert c.post(f"/api/pipelines/p/{op}", json={"new_name": "Q"}).status_code == 400
    assert c.post(f"/api/pipelines/p/{op}", json={"new_name": "p"}).status_code == 400
    assert c.post(f"/api/pipelines/nope/{op}", json={"new_name": "z"}).status_code == 404
    assert c.get("/api/pipelines").json() == ["p", "q"]


def test_rename_is_refused_while_a_task_of_the_pipeline_is_live_but_clone_is_not(client_for):
    c, ports = client_for()
    _made(c)
    folder = c.get("/api/pipelines/p").json()["folder"]
    q = ports["task_queue"]
    q.tasks.append(Task(id="t1", name="preview p", command=[], cwd=folder, status="running"))
    r = c.post("/api/pipelines/p/rename", json={"new_name": "q"})
    assert r.status_code == 409 and "t1" in r.json()["reason"]
    assert c.get("/api/pipelines").json() == ["p"]
    assert c.post("/api/pipelines/p/clone", json={"new_name": "q"}).status_code == 201
    q.tasks[0].status = "succeeded"
    assert c.post("/api/pipelines/p/rename", json={"new_name": "r"}).status_code == 200


# ------------------------------------------------------------------------------------------------ import


def test_export_then_import_round_trips_config_and_file_hashes(client_for, tmp_path):
    c, _ = client_for()
    _made(c)
    outside = tmp_path / "pocket.pdb"
    outside.write_text(PDB)
    doc = c.get("/api/pipelines/p").json()
    toml = doc["toml"] + f'\n[structure]\nspec = "{outside}"\n'
    assert c.put("/api/pipelines/p", json={"toml": toml, "base_sha256": doc["sha256"]}).status_code == 200
    c.put("/api/pipelines/p/files/receptor.pdb", content=PDB.encode())
    zbytes = c.get("/api/pipelines/p/bundle").content

    r = _import(c, zbytes)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["pipeline"]["name"] == "p-2"  # "p" is taken: not overwritten
    assert all(f["ok"] for f in body["files"])
    imported = body["pipeline"]
    assert imported["toml"] == toml.replace(str(outside), "inputs/pocket.pdb")
    sha = {f["name"]: f["sha256"] for f in c.get("/api/pipelines/p-2/files").json()}
    assert sha["receptor.pdb"] == _sha(PDB.encode())
    assert (Path(imported["folder"]) / "inputs" / "pocket.pdb").read_text() == PDB
    # the original is untouched
    assert c.get("/api/pipelines/p").json()["toml"] == toml
    # an explicit name wins; a taken explicit name is a 409
    assert _import(c, zbytes, name="fresh").json()["pipeline"]["name"] == "fresh"
    assert _import(c, zbytes, name="fresh").status_code == 409


def test_a_plain_bundle_imports_under_its_own_name(client_for):
    c, _ = client_for()
    cfg = b'campaign = "x"\n'
    r = _import(c, _bundle({"config.toml": cfg, "r.pdb": PDB.encode()}, top="My_Bundle"))
    assert r.status_code == 201, r.text
    assert r.json()["pipeline"]["name"] == "my_bundle"


def _refused(c, data, *paths):
    r = _import(c, data)
    assert r.status_code in (400, 413, 422), r.text
    assert c.get("/api/pipelines").json() == []  # nothing was created
    if paths:
        got = {f["path"] for f in r.json()["files"] if not f["ok"]}
        assert set(paths) <= got, got
    return r


CFG = b'campaign = "x"\n'


def test_a_dotdot_entry_is_refused_per_file(client_for):
    _refused(
        client_for()[0], _bundle({"config.toml": CFG}, extra={"evil/../../escape.txt": b"x"}), "evil/../../escape.txt"
    )


def test_an_absolute_entry_is_refused_per_file(client_for):
    _refused(client_for()[0], _bundle({"config.toml": CFG}, extra={"/etc/evil": b"x"}), "/etc/evil")


def test_a_symlink_entry_is_refused(client_for):
    buf = io.BytesIO(_bundle({"config.toml": CFG}))
    with zipfile.ZipFile(buf, "a") as z:
        info = zipfile.ZipInfo("evil/link")
        info.external_attr = 0o120777 << 16
        z.writestr(info, "/etc/passwd")
    _refused(client_for()[0], buf.getvalue(), "evil/link")


def test_entries_outside_the_single_top_folder_are_refused(client_for):
    r = _refused(client_for()[0], _bundle({"config.toml": CFG}, extra={"other/x.txt": b"x"}))
    assert "one top folder" in r.json()["reason"]


def test_a_sha_mismatch_is_refused_naming_the_file(client_for):
    files = {"config.toml": CFG, "r.pdb": PDB.encode()}
    man = {"pipeline": "evil", "files": {k: {"sha256": _sha(v)} for k, v in files.items()}}
    man["files"]["r.pdb"]["sha256"] = _sha(b"something else")
    _refused(client_for()[0], _bundle(files, manifest=man), "r.pdb")


def test_unlisted_and_missing_files_are_refused(client_for):
    files = {"config.toml": CFG}
    man = {"pipeline": "evil", "files": {"config.toml": {"sha256": _sha(CFG)}, "gone.pdb": {"sha256": "0"}}}
    _refused(client_for()[0], _bundle(files, extra={"evil/sneaky.pdb": b"x"}, manifest=man), "gone.pdb", "sneaky.pdb")


def test_a_bad_config_is_refused(client_for):
    _refused(client_for()[0], _bundle({"config.toml": b"campaign = = 1\n"}), "config.toml")


def test_not_a_zip_and_no_manifest_are_refused(client_for):
    c, _ = client_for()
    assert _import(c, b"hello").status_code == 400
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("p/config.toml", CFG)
    r = _import(c, buf.getvalue())
    assert r.status_code == 400 and "manifest" in r.json()["reason"]


def test_oversize_bundles_are_refused(client_for, monkeypatch):
    c, _ = client_for()
    data = _bundle({"config.toml": CFG, "r.pdb": PDB.encode() * 100})
    monkeypatch.setattr(bundle_import, "MAX_TOTAL", 1000)
    r = _import(c, data)
    assert r.status_code == 413
    monkeypatch.setattr(bundle_import, "MAX_TOTAL", 256 * 1024 * 1024)
    monkeypatch.setattr(bundle_import, "MAX_FILE", 100)
    assert _import(c, data).status_code == 422  # one file over the per-file cap, named
    monkeypatch.setattr(bundle_import, "MAX_FILE", 64 * 1024 * 1024)
    monkeypatch.setattr(bundle_import, "MAX_ENTRIES", 2)
    assert _import(c, data).status_code == 413
    monkeypatch.setattr(api, "MAX_BUNDLE", 50)
    assert _import(c, data).status_code == 413  # refused while streaming in
    assert c.get("/api/pipelines").json() == []


# ------------------------------------------------------------------------------------------------ review fixes


def _zip(entries: list[tuple[str, bytes]], man: dict, top="t", compression=zipfile.ZIP_STORED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression) as z:
        for rel, b in entries:
            z.writestr(f"{top}/{rel}" if not rel.startswith(top + "/") else rel, b)
        z.writestr(f"{top}/manifest.json", json.dumps(man))
    return buf.getvalue()


def _man(files: dict[str, bytes], **extra) -> dict:
    return {"pipeline": "t", "files": {k: {"sha256": _sha(v)} for k, v in files.items()}, **extra}


def test_a_corrupt_entry_is_a_per_file_422_not_a_500(client_for):
    c, _ = client_for()
    data = bytearray(_bundle({"config.toml": CFG, "r.pdb": PDB.encode()}, top="t"))
    at = bytes(data).index(PDB.encode())
    data[at] ^= 0xFF  # stored, so the bytes are there to damage: the CRC no longer matches
    r = _import(c, bytes(data))
    assert r.status_code == 422, r.text
    assert [f["path"] for f in r.json()["files"] if not f["ok"]] == ["r.pdb"]
    assert c.get("/api/pipelines").json() == []


def test_a_file_and_a_folder_of_the_same_name_are_refused_not_a_500(client_for):
    files = {"config.toml": CFG, "inputs": b"a file", "inputs/x.pdb": PDB.encode()}
    r = _import(client_for()[0], _bundle(files, top="t"))
    assert r.status_code == 422, r.text
    assert any(not f["ok"] for f in r.json()["files"])


def test_duplicate_and_unnormalised_entry_names_are_refused(client_for):
    c, _ = client_for()
    files = {"config.toml": CFG, "x.pdb": PDB.encode()}
    for dup in ("t/x.pdb", "t/./x.pdb"):
        r = _import(c, _bundle(files, top="t", extra={dup: b"other"}))
        assert r.status_code == 422, (dup, r.text)
        assert dup in {f["path"] for f in r.json()["files"] if not f["ok"]}


def test_a_non_object_manifest_entry_is_an_error(client_for):
    man = _man({"config.toml": CFG})
    man["files"]["r.pdb"] = "oops"
    r = _import(client_for()[0], _zip([("config.toml", CFG), ("r.pdb", b"x")], man))
    assert r.status_code == 422 and "r.pdb" in {f["path"] for f in r.json()["files"] if not f["ok"]}


def test_a_bad_name_query_is_a_400(client_for):
    c, _ = client_for()
    data = _bundle({"config.toml": CFG})
    for bad in ("../x", "A b", "x" * 65):
        assert _import(c, data, name=bad).status_code == 400


def test_a_hostile_absolute_input_not_in_the_bundle_is_refused(client_for):
    c, _ = client_for()
    cfg = b'campaign = "x"\n[structure]\nspec = "/home/victim/.ssh/id_rsa"\n'
    r = _import(c, _bundle({"config.toml": cfg}))
    assert r.status_code == 422, r.text
    assert any("structure.spec" in f["path"] for f in r.json()["files"] if not f["ok"])
    cfg = b'campaign = "x"\n[structure]\nspec = "../../secret.pdb"\n'
    assert _import(c, _bundle({"config.toml": cfg})).status_code == 422
    assert c.get("/api/pipelines").json() == []


def test_missing_inputs_from_the_export_are_surfaced_as_notes(client_for):
    man = _man({"config.toml": CFG}, missing=["gone.pdb"])
    r = _import(client_for()[0], _zip([("config.toml", CFG)], man))
    assert r.status_code == 201, r.text
    assert any("gone.pdb" in n for n in r.json()["notes"])


def test_two_absolute_inputs_sharing_a_basename_keep_their_own_content(client_for, tmp_path):
    c, _ = client_for()
    _made(c)
    a, b = tmp_path / "a" / "x.pdb", tmp_path / "b" / "x.pdb"
    a.parent.mkdir()
    b.parent.mkdir()
    a.write_text(PDB)
    b.write_text(PDB + "TER\n")
    doc = c.get("/api/pipelines/p").json()
    toml = doc["toml"].replace("[pocket]\n", f'[pocket]\nfile = "{b}"\n') + f'\n[structure]\nspec = "{a}"\n'
    assert c.put("/api/pipelines/p", json={"toml": toml, "base_sha256": doc["sha256"]}).status_code == 200
    r = _import(c, c.get("/api/pipelines/p/bundle").content, name="q")
    assert r.status_code == 201, r.text
    import tomllib

    got = tomllib.loads(r.json()["pipeline"]["toml"])
    folder = Path(r.json()["pipeline"]["folder"])
    assert (folder / got["structure"]["spec"]).read_text() == PDB
    assert (folder / got["pocket"]["file"]).read_text() == PDB + "TER\n"
    assert got["structure"]["spec"] != got["pocket"]["file"]
