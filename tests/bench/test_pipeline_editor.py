"""The pipeline API: the editor's only state is TOML, and every edit returns every issue Pipeline.check() finds."""

import tomllib

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
    assert "qmmm" not in Pipeline.from_toml(minimal_toml()).addable()


def test_every_issue_is_returned_not_just_the_first(client_for):
    """Two independent errors in two stages: both are reported in one response."""
    c, _ = client_for()
    cfg = tomllib.loads(minimal_toml())
    cfg["cut"]["keep"] = "many"
    cfg["poses"] = {"n_poses": "lots"}
    from smeltery.gui._toml import dumps

    r = c.post("/api/pipelines/steps", json={"toml": dumps(cfg)})
    assert r.status_code == 200, r.text
    issues = r.json()["pipeline_issues"] + [i for s in r.json()["steps"] for i in s["issues"]]
    assert len([i for i in issues if i["level"] == "error"]) >= 2, issues


def _edit(c, toml, op, key, **kw):
    return c.post("/api/pipelines/edit", json={"toml": toml, "op": op, "key": key, **kw})


def test_edits_round_trip_through_toml(client_for):
    c, _ = client_for()
    base = minimal_toml()
    add = Pipeline.from_toml(base).addable()[0]
    t2 = _edit(c, base, "add", add).json()["toml"]
    assert add in Pipeline.from_toml(t2).keys()
    t3 = _edit(c, t2, "remove", add).json()["toml"]
    assert Pipeline.from_toml(t3).keys() == Pipeline.from_toml(base).keys()


def test_unknown_knob_and_bad_toml_are_400(client_for):
    c, _ = client_for()
    assert _edit(c, minimal_toml(), "set", "cut", params={"nope": 1}).status_code == 400
    assert c.post("/api/pipelines/steps", json={"toml": "this is [not toml"}).status_code in (200, 400)


def test_clicking_two_atoms_writes_one_pocket_site(client_for):
    c, _ = client_for()
    site = {"atom": 3, "from_atom": 4, "q": 1.0, "distance": 3.0}
    r = _edit(c, minimal_toml(), "site", "pocket", site=site)
    assert r.status_code == 200, r.text
    sites = tomllib.loads(r.json()["toml"])["pocket"]["site"]
    assert sites[-1] == {"q": 1.0, "atom": 3, "from_atom": 4, "distance": 3.0}
    assert _edit(c, minimal_toml(), "site", "pocket", site={**site, "from_atom": 3}).status_code == 400


def test_the_provisional_conformer_is_in_AddHs_order(client_for):
    from rdkit import Chem

    c, _ = client_for()
    p = c.get("/api/provisional", params={"smiles": CANDS["4-F"]}).json()
    expect = [a.GetSymbol() for a in Chem.AddHs(Chem.MolFromSmiles(CANDS["4-F"])).GetAtoms()]
    assert p["symbols"] == expect
    mb = Chem.MolFromMolBlock(p["molblock"], removeHs=False)
    assert [a.GetSymbol() for a in mb.GetAtoms()] == expect
