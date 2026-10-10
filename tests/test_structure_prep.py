"""The `[structure]` stage without the docking extra: config rules, structure selection, centres, network opt-in.

What needs Meeko / Vina / pdb2pqr30 (the prepared receptor, the frame checks against a real PDBQT) is in
tests/test_docking_structure_cli.py. Every test here must be able to fail: the selection and centre tests assert
numbers computed independently of the code under test.
"""

from __future__ import annotations

import copy
import json
import pathlib
import tomllib

import pytest

from smeltery.cli import ConfigError, StageError, build_plan, validate_config
from smeltery.prep import PrepConfigError, PrepError, clean_receptor, obtain_structure, reference_ligand_centroid
from smeltery.record import RunRecord

DATA = pathlib.Path(__file__).parent / "data"

STRUCT = """
campaign = "s"
parent = "ethanol"

[structure]
spec = "pocket_pep5.pdb"
center = [0.4, -0.1, -0.1]
pocket_cutoff = 12.0

[candidates]
ethanol = "CCO"
propanol = "CCCO"

[docking]
box_size = [22.0, 16.0, 16.0]

[cut]
keep = 1
floor = 0.0
"""


def cfg_of(text=STRUCT):
    return tomllib.loads(text)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda c: c["structure"].pop("pocket_cutoff"), "no default cutoff"),
        (lambda c: c["structure"].update(pocket_cutoff=0), "no default cutoff"),
        (lambda c: c["structure"].update(pocket_cutoff="12"), "no default cutoff"),
        (lambda c: c["structure"].pop("center"), "exactly one of"),
        (lambda c: c["structure"].update(reference_ligand="LIG"), "exactly one of"),
        (lambda c: c["structure"].update(center=[1, 2]), "three numbers"),
        (lambda c: c["structure"].pop("spec"), "spec is required"),
        (lambda c: c["structure"].update(provider="esmfold"), "provider must be"),
        (lambda c: c["structure"].update(allow_network="yes"), "allow_network"),
        (lambda c: c["structure"].update(chains=[]), "chains"),
        (lambda c: c["structure"].update(typo=1), "unknown key"),
        (lambda c: c.update(pocket={"file": "x.pdb"}), r"\[pocket\] cannot be combined"),
        (lambda c: c["docking"].update(receptor="r.pdbqt"), "come from"),
        (lambda c: c["docking"].update(box_center=[0, 0, 0]), "come from"),
        (lambda c: c.update(poses={"n_poses": 4}), "no effect when the parent is docked"),
        (
            lambda c: (c["structure"].pop("center"), c["structure"].update(provider="afdb", reference_ligand="LIG")),
            "experimental structure",
        ),
    ],
)
def test_a_bad_structure_section_is_a_config_error(mutate, message):
    cfg = cfg_of()
    mutate(cfg)
    with pytest.raises(ConfigError, match=message):
        validate_config(cfg)


def test_the_structure_section_is_valid_as_written_and_without_a_docking_table():
    validate_config(cfg_of())
    no_docking = cfg_of()
    del no_docking["docking"]
    validate_config(no_docking)


def test_the_structure_example_config_is_valid():
    validate_config(tomllib.loads((pathlib.Path(__file__).parents[1] / "examples" / "mwe_structure.toml").read_text()))


def test_a_structure_stage_needs_an_output_directory():
    with pytest.raises(ConfigError, match="output directory"):
        build_plan(validate_config(cfg_of()))


PDB = "\n".join(
    [
        "ATOM      1  N   GLY A   1       0.000   0.000   0.000  1.00  0.00           N  ",
        "ATOM      2  CA AGLY A   1       1.000   0.000   0.000  1.00  0.00           C  ",
        "ATOM      3  CA BGLY A   1       9.000   9.000   9.000  1.00  0.00           C  ",
        "ATOM      4  N   ALA B   1      20.000   0.000   0.000  1.00  0.00           N  ",
        "HETATM    5  O   HOH A 101       3.000   3.000   3.000  1.00  0.00           O  ",
        "HETATM    6  C1  LIG A 201       4.000   0.000   0.000  1.00  0.00           C  ",
        "HETATM    7  C2  LIG A 201       6.000   0.000   0.000  1.00  0.00           C  ",
        "HETATM    8  H1  LIG A 201      50.000  50.000  50.000  1.00  0.00           H  ",
        "ENDMDL",
        "ATOM      9  N   GLY A   1     -99.000 -99.000 -99.000  1.00  0.00           N  ",
        "",
    ]
)


def test_clean_receptor_keeps_first_model_protein_altloc_a_and_counts_what_it_drops():
    text, info = clean_receptor(PDB, None)
    kept = [line for line in text.splitlines() if line.startswith("ATOM")]
    assert len(kept) == 3  # N, CA (altloc A), N of chain B; not altloc B, not the second model
    assert all(line[16] == " " for line in kept)  # altloc column blanked
    assert "-99.000" not in text and "9.000   9.000" not in text
    assert info["hetatm_dropped"] == {"HOH": 1, "LIG": 3}
    assert info["n_altloc_dropped"] == 1 and info["n_protein_atoms"] == 3
    only_a, info_a = clean_receptor(PDB, ["A"])
    assert only_a.count("ATOM") == 2 and info_a["chains"] == ["A"]


def test_clean_receptor_refuses_a_structure_with_no_protein():
    with pytest.raises(PrepError, match="no protein ATOM"):
        clean_receptor(PDB, ["Z"])


def test_reference_ligand_centroid_is_the_heavy_atom_mean():
    xyz, info = reference_ligand_centroid(PDB, "LIG")
    assert xyz == (5.0, 0.0, 0.0)  # (4+6)/2; the hydrogen at 50,50,50 is ignored
    assert info == {"residue": "LIG:A:201", "n_heavy_atoms": 2}
    assert reference_ligand_centroid(PDB, "LIG:A:201")[0] == xyz


def test_reference_ligand_must_exist_and_be_unambiguous():
    with pytest.raises(PrepConfigError, match="no such HETATM"):
        reference_ligand_centroid(PDB, "XYZ")
    with pytest.raises(PrepConfigError, match="no such HETATM"):
        reference_ligand_centroid(PDB, "HOH")  # water is never a ligand
    two = PDB.replace(
        "ENDMDL", "HETATM   99  C1  LIG B 202      30.000   0.000   0.000  1.00  0.00           C  \nENDMDL"
    )
    with pytest.raises(PrepConfigError, match="several residues"):
        reference_ligand_centroid(two, "LIG")
    assert reference_ligand_centroid(two, "LIG:B")[0] == (30.0, 0.0, 0.0)


def test_a_pdb_id_needs_the_network_opt_in_and_a_local_file_does_not(tmp_path):
    def boom(url):
        raise AssertionError("the network must not be touched")

    with pytest.raises(PrepConfigError, match="allow_network"):
        obtain_structure("1abc", "pdb", tmp_path, False, boom)
    with pytest.raises(PrepConfigError, match="allow_network"):
        obtain_structure("P69905", "afdb", tmp_path, False, boom)
    res = obtain_structure(str(DATA / "pocket_pep5.pdb"), "pdb", tmp_path, False, boom)
    assert res.kind == "EXPERIMENTAL"
    with pytest.raises(PrepConfigError, match="not an existing"):
        obtain_structure("nope.pdb", "pdb", tmp_path, True, boom)


def test_a_fetched_pdb_id_is_experimental_and_names_its_url(tmp_path):
    seen = []

    def fetch(url):
        seen.append(url)
        return (DATA / "pocket_pep5.pdb").read_bytes()

    res = obtain_structure("1ABC", "pdb", tmp_path, True, fetch)
    assert res.kind == "EXPERIMENTAL" and seen == ["https://files.rcsb.org/download/1ABC.pdb"]
    assert res.source == seen[0]


def test_a_prediction_keeps_its_label_licence_and_attribution(tmp_path):
    tiny = (DATA / "afdb_tiny.pdb").read_bytes()

    def fetch(url):
        if "api/prediction" in url:
            return json.dumps(
                [{"pdbUrl": "https://example.invalid/m.pdb", "latestVersion": 4, "entryId": "AF-X-F1"}]
            ).encode()
        return tiny

    res = obtain_structure("P69905", "afdb", tmp_path, True, fetch)
    d = res.to_dict()
    assert res.kind == "PREDICTED" and d["license_id"] == "CC-BY-4.0" and "CC BY 4.0" in d["attribution"]


def test_a_provider_that_cannot_answer_is_a_stage_error_with_its_reason(tmp_path):
    def fetch(url):
        raise OSError("offline")

    with pytest.raises(PrepError, match="could not fetch"):
        obtain_structure("1ABC", "pdb", tmp_path, True, fetch)


GATED = """
campaign = "g"
parent = "ethanol"

[candidates]
ethanol = "CCO"
propanol = "CCCO"

[poses]
n_poses = 2

[[pocket.site]]
q = 1.0
atom = 2
from_atom = 1
distance = 3.0

[gates]
forcefield = true
max_strain_kcal = 1.0e6

[cut]
keep = 1
floor = 0.0
"""


def _digest(cfg):
    plan = build_plan(cfg)
    return RunRecord(cfg["campaign"], plan.inputs, plan.tiers, {}).input_digest


def test_the_strain_gate_rejects_loudly_and_is_in_the_digest():
    ok = cfg_of(GATED)
    plan = build_plan(validate_config(copy.deepcopy(ok)))
    assert plan.stage_log[-1]["kind"] == "gate" and plan.inputs["gates"]["max_strain_kcal"] == 1.0e6
    tight = copy.deepcopy(ok)
    tight["gates"]["max_strain_kcal"] = 1e-9  # strain is E_mmff - E_mmff_relaxed >= 0 and not exactly 0
    looser = copy.deepcopy(ok)
    looser["gates"]["max_strain_kcal"] = 2.0e6
    assert _digest(looser) != _digest(ok)  # the threshold is a knob: it is in the digest
    with pytest.raises(StageError, match="max_strain_kcal"):
        build_plan(validate_config(tight))
    none = copy.deepcopy(ok)
    del none["gates"]["max_strain_kcal"]
    plan_none = build_plan(validate_config(none))
    assert plan_none.stage_log[-1]["kind"] == "recorded"  # no threshold: recorded, and honest about it
    assert "max_strain_kcal" not in plan_none.inputs["gates"]  # configs from before the knob keep their digests
    assert _digest(none) != _digest(ok)


def test_the_strain_threshold_needs_the_forcefield_gate():
    cfg = cfg_of(GATED)
    cfg["gates"]["forcefield"] = False
    with pytest.raises(ConfigError, match="needs forcefield = true"):
        validate_config(cfg)
    cfg["gates"].update(forcefield=True, max_strain_kcal=-1)
    with pytest.raises(ConfigError, match="positive number"):
        validate_config(cfg)


def test_the_funnel_counts_every_stage_and_marks_the_cut_unranked():
    from smeltery.cli import funnel_counts
    from smeltery.funnel import CutResult

    plan = build_plan(validate_config(cfg_of(GATED)))
    plan_rows = funnel_counts(plan)
    assert [r["stage"] for r in plan_rows] == ["poses", "pocket", "parity", "forcefield"]
    assert all(r["candidates_in"] == 2 == r["candidates_out"] for r in plan_rows)
    ranked = funnel_counts(plan, CutResult([["a"], ["b"]], ["a"], False, 2.0, 0.0))[-2:]
    assert ranked[0] == {"stage": "field_interaction", "kind": "measure", "candidates_in": 2, "candidates_out": 2}
    assert ranked[1]["stage"] == "cut" and ranked[1]["candidates_in"] == 1 and ranked[1]["candidates_out"] == 1
    unranked = funnel_counts(plan, CutResult([["a", "b"]], None, True, 2.0, 0.0, ["x"]))[-1]
    assert unranked["candidates_out"] is None and unranked["unranked"] is True
