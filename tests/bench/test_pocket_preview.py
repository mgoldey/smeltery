"""POST /api/pipelines/pocket_preview: the field `smeltery run` would build, with no docking and no SCF."""

import shutil
from pathlib import Path

import pytest

from smeltery.pocket.loader import load_pocket

PDB = Path(__file__).resolve().parents[1] / "data" / "pocket_pep5.pdb"
needs_pdb2pqr = pytest.mark.skipif(shutil.which("pdb2pqr30") is None, reason="pdb2pqr30 not installed")


def _toml(extra=""):
    return f'campaign = "c"\nparent = "a"\n[candidates]\na = "CCO"\n{extra}'


@needs_pdb2pqr
def test_a_pocket_file_preview_matches_load_pocket(client_for):
    c, _ = client_for()
    want = load_pocket(PDB)  # data source: the library the run uses, called directly
    box = [float(v) for v in want[0].xyz_ang]
    r = c.post(
        "/api/pipelines/pocket_preview",
        json={"toml": _toml(f'[pocket]\nfile = "{PDB}"\n[docking]\nbox_center = {box}\n')},
    )
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["n_charges"] == len(want) > 0
    assert got["net_charge"] == pytest.approx(sum(q.q for q in want))
    assert got["nearest_ang"] == pytest.approx(0.0, abs=1e-9)
    nodes = str(got["scene"])
    assert "primitives" in nodes and got["scene"]["kind"] == "single"
    spheres = [n for n in got["scene"]["root"]["children"] if n["kind"] == "primitives"]
    assert len(spheres[0]["children"]) == len(want)


def test_a_missing_pdb2pqr_is_409_and_named(client_for, monkeypatch):
    c, _ = client_for()
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: None)
    r = c.post("/api/pipelines/pocket_preview", json={"toml": _toml(f'[pocket]\nfile = "{PDB}"\n')})
    assert r.status_code == 409
    assert "pdb2pqr30" in r.json()["detail"]


def test_a_structure_without_pdb2pqr_is_409_and_named(client_for, monkeypatch):
    c, _ = client_for()
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: None)
    s = f'[structure]\nspec = "{PDB}"\ncenter = [0.0, 0.0, 0.0]\npocket_cutoff = 8.0\n'
    r = c.post("/api/pipelines/pocket_preview", json={"toml": _toml(s)})
    assert r.status_code == 409 and "pdb2pqr30" in r.json()["detail"]


def test_a_pqr_needs_no_pdb2pqr(client_for, monkeypatch, tmp_path):
    c, _ = client_for()
    pqr = Path(__file__).resolve().parents[1] / "data" / "gly3.pqr"
    want = load_pocket(pqr)
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: None)
    r = c.post("/api/pipelines/pocket_preview", json={"toml": _toml(f'[pocket]\nfile = "{pqr}"\n')})
    assert r.status_code == 200, r.text
    assert r.json()["n_charges"] == len(want) and r.json()["nearest_ang"] is None


@needs_pdb2pqr
def test_a_structure_preview_cuts_around_the_centre(client_for):
    c, _ = client_for()
    centre = [float(v) for v in load_pocket(PDB)[0].xyz_ang]
    s = f'[structure]\nspec = "{PDB}"\ncenter = {centre}\npocket_cutoff = 4.0\n'
    r = c.post("/api/pipelines/pocket_preview", json={"toml": _toml(s)})
    assert r.status_code == 200, r.text
    got = r.json()
    want = load_pocket(PDB, cutoff_ang=4.0, center_ang=tuple(centre))
    assert got["n_charges"] == len(want) and 0 < len(want) < len(load_pocket(PDB))
    assert got["nearest_ang"] <= 4.0


def test_hand_placed_charges_are_counted_without_a_scene(client_for):
    c, _ = client_for()
    one = "[[pocket.site]]\natom = {}\nfrom_atom = {}\nq = {}\ndistance = 3.0\n"
    site = one.format(1, 0, 1.0) + one.format(2, 1, -0.5)
    r = c.post("/api/pipelines/pocket_preview", json={"toml": _toml(site)})
    assert r.status_code == 200
    assert r.json()["n_charges"] == 2 and r.json()["net_charge"] == pytest.approx(0.5) and r.json()["scene"] is None


def test_nothing_placed_and_bad_toml_are_400(client_for):
    c, _ = client_for()
    assert c.post("/api/pipelines/pocket_preview", json={"toml": _toml()}).status_code == 400
    assert c.post("/api/pipelines/pocket_preview", json={"toml": "= ="}).status_code == 400


def _blank(c):
    r = c.post(
        "/api/pipelines",
        json={"name": "p", "blank": {"campaign": "c", "parent": "a", "candidates": {"a": "CCO", "b": "CCN"}}},
    )
    assert r.status_code == 201, r.text
    return r.json()["toml"]


def test_choosing_a_structure_writes_structure_and_docking_and_keeps_comments(client_for):
    import tomllib

    c, _ = client_for()
    text = _blank(c) + "\n# my note\n"
    r = c.post(
        "/api/pipelines/active_site",
        json={"toml": text, "choice": "structure", "spec": str(PDB), "center": [1, 2, 3], "pocket_cutoff": 10.0},
    )
    assert r.status_code == 200, r.text
    out = r.json()["toml"]
    cfg = tomllib.loads(out)
    assert cfg["structure"] == {"spec": str(PDB), "center": [1.0, 2.0, 3.0], "pocket_cutoff": 10.0}
    assert "docking" in cfg and "file" not in cfg.get("pocket", {})
    assert "# my note" in out


def test_choosing_a_pocket_file_then_hand_placed_round_trips(client_for):
    import tomllib

    c, _ = client_for()
    text = _blank(c)
    r = c.post(
        "/api/pipelines/active_site",
        json={
            "toml": text,
            "choice": "pocket_file",
            "file": "pocket.pqr",
            "receptor": "rec.pdbqt",
            "box_center": [0, 0, 0],
            "box_size": [20, 20, 20],
        },
    )
    assert r.status_code == 200, r.text
    cfg = tomllib.loads(r.json()["toml"])
    assert cfg["pocket"]["file"] == "pocket.pqr"
    assert cfg["docking"] == {"receptor": "rec.pdbqt", "box_center": [0.0, 0.0, 0.0], "box_size": [20.0, 20.0, 20.0]}
    back = c.post("/api/pipelines/active_site", json={"toml": r.json()["toml"], "choice": "hand_placed"})
    assert back.status_code == 200, back.text
    cfg = tomllib.loads(back.json()["toml"])
    assert "docking" not in cfg and "structure" not in cfg and "file" not in cfg["pocket"]


def test_an_incomplete_choice_is_400_naming_what_is_missing(client_for):
    c, _ = client_for()
    r = c.post("/api/pipelines/active_site", json={"toml": _blank(c), "choice": "structure", "spec": "x.pdb"})
    assert r.status_code == 400 and "pocket_cutoff" in r.json()["detail"]


@pytest.mark.skipif(not (Path(__file__).resolve().parents[1] / "data" / "gly3.pqr").exists(), reason="no fixture")
def test_a_relative_pocket_file_resolves_against_the_saved_pipeline_folder(client_for, monkeypatch, tmp_path):
    c, _ = client_for()
    text = _blank(c)
    folder = Path(c.get("/api/pipelines/p").json()["folder"])
    pqr = Path(__file__).resolve().parents[1] / "data" / "gly3.pqr"
    (folder / "pocket.pqr").write_text(pqr.read_text())
    monkeypatch.chdir(tmp_path)  # the server cwd is NOT the pipeline folder
    assert "[pocket]" in text
    toml = text.replace("[pocket]", '[pocket]\nfile = "pocket.pqr"', 1)
    ok = c.post("/api/pipelines/pocket_preview", json={"toml": toml, "pipeline": "p"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["n_charges"] == len(load_pocket(pqr))
    assert c.post("/api/pipelines/pocket_preview", json={"toml": toml}).status_code == 400  # no pipeline: cwd


@pytest.mark.parametrize("site", ["file", "structure"])
def test_a_pdb2pqr_that_is_found_but_cannot_run_is_409_not_500(client_for, monkeypatch, site):
    # A broken shim (pyenv's, for one) passes the "is it there" check and fails only when the loader runs it.
    from smeltery.bench.api import active_site
    from smeltery.pocket.loader import Pdb2PqrUnavailableError

    def broken(*a, **k):
        raise Pdb2PqrUnavailableError("`/shims/pdb2pqr30` is on PATH but `--version` fails (a broken shim?)")

    monkeypatch.setattr(active_site, "pdb2pqr_exe", lambda: "/shims/pdb2pqr30")
    monkeypatch.setattr(active_site, "load_pocket", broken)
    extra = (
        f'[pocket]\nfile = "{PDB}"\n'
        if site == "file"
        else f'[structure]\nspec = "{PDB}"\ncenter = [0.0, 0.0, 0.0]\npocket_cutoff = 8.0\n'
    )
    c, _ = client_for()
    r = c.post("/api/pipelines/pocket_preview", json={"toml": _toml(extra)})
    assert r.status_code == 409, r.text
    assert "broken shim" in r.json()["detail"]
