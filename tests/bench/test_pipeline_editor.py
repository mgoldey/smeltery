"""The pipeline API: the editor's only state is TOML, and every edit returns every issue Pipeline.check() finds."""

import sys
import tomllib
from pathlib import Path

from bench.fakes import FakeTaskQueue
from smeltery.gui.catalog import RUNGS, catalog
from smeltery.gui.pipeline import Pipeline

CANDS = {"benzoic": "OC(=O)c1ccccc1", "4-F": "OC(=O)c1ccc(F)cc1"}


def minimal_toml():
    """A pipeline the CLI accepts: the minimal one plus the two things it cannot default (keep, and an explicit
    floor, because the field tier's own floor is unmeasured)."""
    p = Pipeline.minimal("t", "benzoic", dict(CANDS))
    p.set("cut", keep=1, floor=0.0)
    p.set("pocket", site=[{"q": 1.0, "atom": 1, "from_atom": 2, "distance": 3.0}])
    return p.to_toml()


def test_the_catalog_is_catalog_json_plus_quantity_phase_and_rigour(client_for):
    c, _ = client_for()
    cat = c.get("/api/catalog").json()
    assert [r["name"] for r in cat["rungs"]] == [name for name, _ in RUNGS]
    for key, m in catalog().items():
        assert cat["methods"][key]["quantity"] == m.quantity and cat["methods"][key]["rigour"] == m.rigour, key


def test_an_unwired_tier_is_listed_with_a_reason_but_not_addable(client_for):
    c, _ = client_for()
    cat = c.get("/api/catalog").json()
    assert cat["methods"]["qmmm"]["wired"] is False and cat["methods"]["qmmm"]["why_unwired"]
    state = c.post("/api/pipeline/check", json={"toml": minimal_toml()}).json()
    assert "qmmm" not in state["addable"]


def test_every_issue_is_returned_not_just_the_first(client_for):
    """Two independent errors in two stages: both are reported in one response."""
    c, _ = client_for()
    cfg = tomllib.loads(minimal_toml())
    cfg["cut"]["keep"] = "many"
    cfg["poses"] = {"n_poses": "lots"}
    from smeltery.gui._toml import dumps

    state = c.post("/api/pipeline/check", json={"toml": dumps(cfg)}).json()
    assert state["ok"] is False
    assert len([i for i in state["issues"] if i["level"] == "error"]) >= 2, state["issues"]


def test_edits_round_trip_through_toml(client_for):
    c, _ = client_for()
    s1 = c.post("/api/pipeline/check", json={"toml": minimal_toml()}).json()
    add = s1["addable"][0]
    s2 = c.post("/api/pipeline/edit", json={"toml": s1["toml"], "op": "add", "key": add}).json()
    assert add in Pipeline.from_toml(s2["toml"]).keys()
    s3 = c.post("/api/pipeline/edit", json={"toml": s2["toml"], "op": "remove", "key": add}).json()
    assert Pipeline.from_toml(s3["toml"]).keys() == Pipeline.from_toml(s1["toml"]).keys()


def test_unknown_knob_and_bad_toml_are_400(client_for):
    c, _ = client_for()
    r = c.post("/api/pipeline/edit", json={"toml": minimal_toml(), "op": "set", "key": "cut", "params": {"nope": 1}})
    assert r.status_code == 400
    assert c.post("/api/pipeline/check", json={"toml": "this is [not toml"}).status_code == 400


def test_clicking_two_atoms_writes_one_pocket_site(client_for):
    c, _ = client_for()
    r = c.post(
        "/api/pipeline/site", json={"toml": minimal_toml(), "atom": 3, "from_atom": 4, "q": 1.0, "distance": 3.0}
    )
    assert r.status_code == 200
    sites = tomllib.loads(r.json()["toml"])["pocket"]["site"]
    assert sites[-1] == {"q": 1.0, "atom": 3, "from_atom": 4, "distance": 3.0}
    assert c.post("/api/pipeline/site", json={"toml": minimal_toml(), "atom": 3, "from_atom": 3}).status_code == 400


def test_the_provisional_conformer_is_in_AddHs_order(client_for):
    from rdkit import Chem

    c, _ = client_for()
    p = c.get("/api/provisional", params={"smiles": CANDS["4-F"]}).json()
    expect = [a.GetSymbol() for a in Chem.AddHs(Chem.MolFromSmiles(CANDS["4-F"])).GetAtoms()]
    assert p["symbols"] == expect
    mb = Chem.MolFromMolBlock(p["molblock"], removeHs=False)
    assert [a.GetSymbol() for a in mb.GetAtoms()] == expect


def test_launch_is_refused_with_errors_and_stages_inputs_into_out_when_clean(client_for, tmp_path):
    q = FakeTaskQueue()
    c, ports = client_for(queue=q)
    cfg = tomllib.loads(minimal_toml())
    cfg["parent"] = "nobody"
    from smeltery.gui._toml import dumps

    assert c.post("/api/pipeline/launch", json={"toml": dumps(cfg)}).status_code == 409
    assert q.submitted == []
    out = tmp_path / "launched"
    r = c.post("/api/pipeline/launch", json={"toml": minimal_toml(), "out": str(out)})
    assert r.status_code == 201, r.json()
    (name, argv, _cwd, _note) = q.submitted[0]
    from smeltery.bench.use_cases.launch_run import smeltery_command

    assert argv == [*smeltery_command(), "run", str(out / "config.toml"), "--out", str(out)]
    # The venv's own smeltery (or this interpreter running the module), never whatever `smeltery` PATH finds.
    assert Path(argv[0]).parent == Path(sys.executable).parent
    assert Path(out / "config.toml").is_file()


def test_from_run_restores_the_cut_and_says_what_it_cannot_restore(client_for, run_dir):
    """Data source: the run record's inputs (keep, z, and a floor whose source is the config)."""
    import json

    from bench.conftest import record_path

    rec = json.loads(record_path(run_dir).read_text())
    c, _ = client_for(run_dir)
    state = c.post("/api/pipeline/from_run", json={"run": 0}).json()
    cut = tomllib.loads(state["toml"])["cut"]
    assert cut["keep"] == rec["inputs"]["cut"]["keep"] and cut["z"] == rec["inputs"]["cut"]["z"]
    assert cut["floor"] == rec["inputs"]["floor"]["value"]
    assert any("pocket charges" in n for n in state["notes"])


def test_from_run_carries_forward_every_gate_that_ran(client_for, run_dir):
    """Data source: the record's inputs.gates, with xtb switched on; the TOML gate name xtb is the gfn2 stage."""
    import json

    from bench.conftest import record_path

    rp = record_path(run_dir)
    rec = json.loads(rp.read_text())
    rec["inputs"]["gates"]["xtb"] = True
    rp.write_text(json.dumps(rec))
    c, _ = client_for(run_dir)
    state = c.post("/api/pipeline/from_run", json={"run": 0}).json()
    assert "gfn2" in state["placed"] and tomllib.loads(state["toml"])["gates"]["xtb"] is True
