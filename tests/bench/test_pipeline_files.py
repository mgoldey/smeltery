"""Input files of a pipeline: a structure picked in the browser is uploaded into the pipeline's folder, and the
structure the config names is opened from that folder, so the Method field, the picker and the preview agree."""

import hashlib
import io
import json
import zipfile
from pathlib import Path

PDB = (
    "ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00  0.00           N\n"
    "HETATM  101  C   LIG A 100       2.000   2.000   2.000  1.00  0.00           C\n"
)
BLANK = {"campaign": "c", "parent": "a", "candidates": {"a": "CCO", "b": "CCN"}}


def _made(c, name="p"):
    assert c.post("/api/pipelines", json={"name": name, "blank": BLANK}).status_code == 201
    return name


def test_an_upload_lands_in_the_pipeline_folder_and_is_listed(client_for):
    c, _ = client_for()
    name = _made(c)
    r = c.put(f"/api/pipelines/{name}/files/receptor.pdb", content=PDB.encode())
    assert r.status_code == 200, r.text
    assert r.json()["path"] == "receptor.pdb"
    listed = c.get(f"/api/pipelines/{name}/files").json()
    assert [(f["name"], f["size"]) for f in listed] == [("receptor.pdb", len(PDB))]
    assert listed[0]["sha256"] == hashlib.sha256(PDB.encode()).hexdigest()
    folder = c.get(f"/api/pipelines/{name}").json()["folder"]
    from pathlib import Path

    assert (Path(folder) / "receptor.pdb").read_text() == PDB


def test_an_upload_with_a_path_in_its_name_is_refused(client_for):
    c, _ = client_for()
    name = _made(c)
    for bad in ("..%2Fx.pdb", "config.toml", ".x.pdb"):
        r = c.put(f"/api/pipelines/{name}/files/{bad}", content=b"x")
        assert r.status_code in (400, 404), (bad, r.status_code)
    assert c.get(f"/api/pipelines/{name}/files").json() == []


def test_an_upload_to_no_pipeline_is_404(client_for):
    c, _ = client_for()
    assert c.put("/api/pipelines/nope/files/x.pdb", content=b"x").status_code == 404


def test_a_relative_structure_opens_from_the_pipeline_folder_not_the_server_cwd(client_for):
    # The config says `spec = "receptor.pdb"`, relative to the pipeline folder; opening it must find that file.
    c, _ = client_for()
    name = _made(c)
    c.put(f"/api/pipelines/{name}/files/receptor.pdb", content=PDB.encode())
    r = c.post("/api/structure", json={"source": "receptor.pdb", "pipeline": name})
    assert r.status_code == 200, r.text
    assert r.json()["n_atoms"] == 2 and "LIG" in r.json()["dropped"]
    assert c.post("/api/structure", json={"source": "missing.pdb", "pipeline": name}).status_code == 400


def test_the_steps_mark_the_settings_that_name_an_input_file(client_for):
    # The Method column offers a file picker exactly where the run would read a file (the methods' file_knobs).
    c, _ = client_for()
    toml = (
        'campaign = "c"\nparent = "a"\n[candidates]\na = "CCO"\nb = "CCN"\n'
        '[structure]\nspec = "r.pdb"\ncenter = [0.0, 0.0, 0.0]\npocket_cutoff = 8.0\n[docking]\n'
    )
    r = c.post("/api/pipelines/steps", json={"toml": toml})
    assert r.status_code == 200, r.text
    params = {(p["method"], p["name"]): p["names_file"] for s in r.json()["steps"] for p in s["params"]}
    assert params[("structure", "spec")] is True
    assert params[("docking", "receptor")] is True
    assert params[("structure", "pocket_cutoff")] is False


def test_an_upload_over_the_limit_is_refused_on_its_declared_length(client_for):
    # Refused from Content-Length, before the body is buffered into memory.
    from smeltery.bench.api.pipelines import MAX_UPLOAD

    c, _ = client_for()
    name = _made(c)
    r = c.put(
        f"/api/pipelines/{name}/files/huge.pdb",
        content=b"x",
        headers={"content-length": str(MAX_UPLOAD + 1)},
    )
    assert r.status_code == 413 and "larger than" in r.json()["reason"]
    assert c.get(f"/api/pipelines/{name}/files").json() == []


def test_bundle_zip_holds_config_inputs_and_a_manifest_that_matches(client_for):
    c, _ = client_for()
    name = _made(c)
    c.put(f"/api/pipelines/{name}/files/receptor.pdb", content=PDB.encode())
    r = c.get(f"/api/pipelines/{name}/bundle")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert sorted(z.namelist()) == [f"{name}/{n}" for n in ("config.toml", "manifest.json", "receptor.pdb")]
    manifest = json.loads(z.read(f"{name}/manifest.json"))
    assert manifest["pipeline"] == name and manifest["smeltery"]
    for n, info in manifest["files"].items():
        data = z.read(f"{name}/{n}")
        assert (info["sha256"], info["size"]) == (hashlib.sha256(data).hexdigest(), len(data))
    assert set(manifest["files"]) == {"config.toml", "receptor.pdb"}


def test_bundle_includes_nested_and_absolute_inputs_and_lists_missing(client_for, tmp_path):
    c, _ = client_for()
    name = _made(c)
    outside = tmp_path / "pocket.pdb"
    outside.write_text(PDB)
    doc = c.get(f"/api/pipelines/{name}").json()
    nested = Path(doc["folder"]) / "inputs" / "abc"
    nested.mkdir(parents=True)
    (nested / "r.pdb").write_text(PDB)
    toml = doc["toml"].replace("[pocket]\n", f'[pocket]\nfile = "{outside}"\n')
    toml += '\n[structure]\nspec = "inputs/abc/r.pdb"\n[docking]\nreceptor = "gone.pdb"\n'
    put = c.put(f"/api/pipelines/{name}", json={"toml": toml, "base_sha256": doc["sha256"]})
    assert put.status_code == 200, (put.text, doc["toml"])
    z = zipfile.ZipFile(io.BytesIO(c.get(f"/api/pipelines/{name}/bundle").content))
    manifest = json.loads(z.read(f"{name}/manifest.json"))
    assert f"{name}/inputs/abc/r.pdb" in z.namelist() and f"{name}/inputs/pocket.pdb" in z.namelist()
    assert manifest["files"]["inputs/pocket.pdb"]["original_path"] == str(outside)
    assert manifest["missing"] == ["gone.pdb"]
