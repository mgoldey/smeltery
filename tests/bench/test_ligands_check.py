"""The Ligands step's checks and edits: per-candidate facts that need no SCF and no docking."""

import tomllib

from smeltery.gui._toml import dumps
from smeltery.gui.pipeline import Pipeline
from smeltery.model import Candidate
from smeltery.tiers import PairedPoses

PARENT = "OC(=O)c1ccccc1"
CANDS = {"benzoic": PARENT, "4-F": "OC(=O)c1ccc(F)cc1"}


def toml_of(cands=None, parent="benzoic"):
    p = Pipeline.minimal("t", parent, dict(cands or CANDS))
    return "# my pipeline\n" + dumps(p.to_config())


def check(client, cands=None, parent="benzoic"):
    r = client.post("/api/pipelines/ligands", json={"toml": toml_of(cands, parent)})
    assert r.status_code == 200, r.text
    return {c["name"]: c for c in r.json()["candidates"]}


def test_a_bromo_analogue_fails_sto3g_and_names_the_element(client_for):
    c, _ = client_for()
    got = check(c, {**CANDS, "4-Br": "OC(=O)c1ccc(Br)cc1"})
    assert got["4-Br"]["sto3g_ok"] is False
    assert got["4-Br"]["sto3g_missing"] == ["Br"]
    assert any("Br" in m for m in got["4-Br"]["problems"])
    assert got["4-F"]["sto3g_ok"] is True and got["4-F"]["sto3g_missing"] == []


def test_an_odd_electron_radical_is_not_even(client_for):
    c, _ = client_for()
    got = check(c, {**CANDS, "radical": "[O]C(=O)c1ccccc1"})  # benzoic acid minus the O-H hydrogen: 61 electrons
    assert got["radical"]["electrons_even"] is False
    assert got["benzoic"]["electrons_even"] is True
    assert got["radical"]["electrons"] % 2 == 1


def test_charge_enters_the_electron_count_like_the_cli_parity_check(client_for):
    c, _ = client_for()
    got = check(c, {**CANDS, "benzoate": "[O-]C(=O)c1ccccc1"})
    assert got["benzoate"]["formal_charge"] == -1
    assert got["benzoate"]["electrons_even"] is True  # 1 fewer H, 1 extra electron: still even
    assert got["benzoate"]["electrons"] == got["benzoic"]["electrons"]


def test_core_atoms_are_the_scaffold_map_paired_poses_uses(client_for):
    c, _ = client_for()
    got = check(c)
    parent = Candidate("benzoic", PARENT)
    analogue = Candidate("4-F", CANDS["4-F"])
    tier = PairedPoses(n_poses=1)
    tier.run([parent, analogue], {"parent": parent})
    expected = sorted(a for a, _ in tier.scaffold_maps["4-F"])
    assert got["4-F"]["core_atoms"] == expected
    # every atom but the F and the hydrogen the F took the place of on the parent
    n = len(expected)
    from rdkit import Chem

    mol = Chem.AddHs(Chem.MolFromSmiles(CANDS["4-F"]))
    f = [a.GetIdx() for a in mol.GetAtoms() if a.GetSymbol() == "F"]
    assert set(range(mol.GetNumAtoms())) - set(expected) == set(f)
    assert n == mol.GetNumAtoms() - 1
    assert got["benzoic"]["core_atoms"] == list(range(Chem.AddHs(Chem.MolFromSmiles(PARENT)).GetNumAtoms()))


def test_an_unparseable_smiles_is_reported_not_raised(client_for):
    c, _ = client_for()
    got = check(c, {**CANDS, "junk": "C(C"})
    j = got["junk"]
    assert j["smiles_ok"] is False and j["electrons_even"] is None and j["sto3g_ok"] is None
    assert j["core_atoms"] == [] and j["problems"]


def test_no_common_core_is_a_reason_not_a_failure(client_for):
    c, _ = client_for()
    got = check(c, {**CANDS, "ethane": "CC"})
    assert got["ethane"]["core_atoms"] == []
    assert any("core" in m for m in got["ethane"]["problems"])


def test_text_that_is_not_toml_is_a_400_naming_the_line_and_an_empty_pipeline_has_no_rows(client_for):
    c, _ = client_for()
    r = c.post("/api/pipelines/ligands", json={"toml": "a = 1\nb = [\n"})
    assert r.status_code == 400 and "line" in r.json()["detail"]
    assert c.post("/api/pipelines/ligands", json={"toml": "x = 1\n"}).json()["candidates"] == []


def edit(client, cands, parent, text=None):
    rows = cands if isinstance(cands, list) else [{"name": n, "smiles": s} for n, s in cands.items()]
    return client.post(
        "/api/pipelines/ligands/edit", json={"toml": text or toml_of(), "candidates": rows, "parent": parent}
    )


def test_an_edit_writes_candidates_and_keeps_the_comment(client_for):
    c, _ = client_for()
    r = edit(c, {**CANDS, "4-Cl": "OC(=O)c1ccc(Cl)cc1"}, "benzoic")
    assert r.status_code == 200, r.text
    text = r.json()["toml"]
    assert text.startswith("# my pipeline\n")
    cfg = tomllib.loads(text)
    assert cfg["candidates"]["4-Cl"] == "OC(=O)c1ccc(Cl)cc1" and cfg["parent"] == "benzoic"


def test_changing_a_smiles_and_renaming_the_parent(client_for):
    c, _ = client_for()
    cands = {"acid": PARENT, "4-F": "OC(=O)c1ccc(F)cc1"}
    cfg = tomllib.loads(edit(c, cands, "acid").json()["toml"])
    assert cfg["candidates"] == cands and cfg["parent"] == "acid"


def test_removing_the_parent_is_refused_with_the_reason(client_for):
    c, _ = client_for()
    r = edit(c, {"4-F": CANDS["4-F"]}, "benzoic")
    assert r.status_code == 400
    assert "parent" in r.json()["detail"] and "benzoic" in r.json()["detail"]
    r = edit(c, {"4-F": CANDS["4-F"]}, "4-F")  # picking another parent first makes the removal fine
    assert r.status_code == 200 and "benzoic" not in tomllib.loads(r.json()["toml"])["candidates"]


def test_a_parent_that_is_not_a_candidate_and_a_blank_name_are_refused(client_for):
    c, _ = client_for()
    assert edit(c, CANDS, "nope").status_code == 400
    assert edit(c, {**CANDS, "": "CC"}, "benzoic").status_code == 400


def test_a_duplicate_name_is_refused_naming_it(client_for):
    c, _ = client_for()
    r = edit(
        c,
        [{"name": "a", "smiles": "CC"}, {"name": "benzoic", "smiles": PARENT}, {"name": "a", "smiles": "CCC"}],
        "benzoic",
    )
    assert r.status_code == 400 and "'a'" in r.json()["detail"]


def test_a_repeated_pair_is_computed_once(client_for, monkeypatch):
    from smeltery.bench.api import ligands

    c, _ = client_for()
    ligands._core_cached.cache_clear()
    calls = []
    real = PairedPoses.run
    monkeypatch.setattr(PairedPoses, "run", lambda self, *a, **k: (calls.append(1), real(self, *a, **k))[1])
    check(c)
    check(c)
    check(c, {**CANDS, "4-Cl": "OC(=O)c1ccc(Cl)cc1"})
    assert len(calls) == 2  # 4-F once, 4-Cl once; the repeats were cache hits


def test_the_check_searches_as_long_as_a_run_does_and_a_bad_molecule_is_a_row(client_for, monkeypatch):
    from smeltery.bench.api import ligands

    c, _ = client_for()
    ligands._core_cached.cache_clear()
    seen = []
    real = PairedPoses.run

    def run(self, *a, **k):
        seen.append(self.mcs_timeout_s)
        return real(self, *a, **k)

    monkeypatch.setattr(PairedPoses, "run", run)
    check(c)
    assert seen == [PairedPoses().mcs_timeout_s]

    def boom(self, *a, **k):
        raise MemoryError("boom")

    monkeypatch.setattr(PairedPoses, "run", boom)
    got = check(c, {**CANDS, "4-Cl": "OC(=O)c1ccc(Cl)cc1"})
    assert got["4-Cl"]["core_atoms"] == [] and "boom" in got["4-Cl"]["problems"][0]


def test_a_missing_parent_is_one_list_level_problem(client_for):
    c, _ = client_for()
    r = c.post("/api/pipelines/ligands", json={"toml": toml_of(CANDS, "ghost")}).json()
    assert len(r["problems"]) == 1 and "ghost" in r["problems"][0]
    assert all(not any("SMILES cannot be read" in m for m in x["problems"]) for x in r["candidates"])
