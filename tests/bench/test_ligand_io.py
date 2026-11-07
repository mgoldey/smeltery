"""Importing candidates from CSV/SDF into the draft, and exporting them."""

import tomllib

from smeltery.bench import ligand_io
from tests.bench.test_ligands_check import CANDS, toml_of


def test_csv_and_sdf_parse_and_a_bad_row_is_rejected_with_its_reason():
    rows, bad = ligand_io.parse(
        "x.csv", "ID,SMILES\nbenzoic,OC(=O)c1ccccc1\nbroken,not-a-smiles\n,OC(=O)c1ccc(Cl)cc1\n"
    )
    new, rejected = ligand_io.accept(rows, bad, {"4-F": "OC(=O)c1ccc(F)cc1"})
    assert list(new) == ["benzoic", "imported_3"]
    assert [(r.row, r.reason) for r in rejected] == [(2, "RDKit cannot read the SMILES")]
    sdf = ligand_io.to_sdf(
        [
            {
                "name": "a",
                "smiles": "CCO",
                "role": "analogue",
                "formal_charge": 0,
                "electrons": 26,
                "electrons_even": True,
                "sto3g_ok": True,
            }
        ]
    )
    rows, bad = ligand_io.parse("x.sdf", sdf)
    assert [(r.name, r.smiles) for r in rows] == [("a", "CCO")] and not bad


def test_import_appends_to_the_draft_and_duplicates_are_rejected(client_for):
    c, _ = client_for()
    csv_text = "name,smiles\nbenzoic,CC\n4-Cl,OC(=O)c1ccc(Cl)cc1\ndup-mol,OC(=O)c1ccc(F)cc1\n"
    r = c.post("/api/pipelines/ligands/import", json={"toml": toml_of(), "filename": "c.csv", "content": csv_text})
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["accepted"] == ["4-Cl"]
    assert [x["row"] for x in got["rejected"]] == [1, 3]
    assert list(tomllib.loads(got["toml"])["candidates"]) == [*CANDS, "4-Cl"]


def test_export_csv_and_sdf_are_attachments(client_for):
    c, _ = client_for()
    c.post(
        "/api/pipelines",
        json={"name": "p1", "blank": {"campaign": "x", "parent": "a", "candidates": {"a": "CCO", "b": "CCN"}}},
    )
    r = c.get("/api/pipelines/p1/ligands.csv")
    assert r.status_code == 200, r.text
    assert 'filename="p1-ligands.csv"' in r.headers["content-disposition"]
    assert r.text.splitlines()[:2] == [
        "name,smiles,role,formal_charge,electrons,electrons_even,sto3g_ok",
        "a,CCO,parent,0,26,True,True",
    ]
    r = c.get("/api/pipelines/p1/ligands.sdf")
    assert r.text.count("$$$$") == 2 and ">  <role>" in r.text
