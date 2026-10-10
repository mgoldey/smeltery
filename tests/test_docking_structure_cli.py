"""`smeltery run` from a TARGET: the `[structure]` stage with real Meeko, Vina and pdb2pqr30 (issue #17).

The structure is the committed pentapeptide (tests/data/pocket_pep5.pdb) read through the PdbProvider; the receptor
PDBQT, the docking box and the pocket field are all prepared from it under the output directory. Skips (with the
install command) unless the docking extra and pdb2pqr30 are installed; CI's `docking` job installs both and fails if
any of these skip for a missing module.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys

import numpy as np
import pytest

_MISSING = [m for m in ("vina", "meeko", "rdkit") if importlib.util.find_spec(m) is None]
pytestmark = [
    pytest.mark.skipif(
        bool(_MISSING),
        reason=f"needs the docking extra: pip install 'smeltery[docking]' ({', '.join(_MISSING)} missing)",
    ),
    pytest.mark.skipif(shutil.which("pdb2pqr30") is None, reason="pdb2pqr30 not installed (pip install pdb2pqr)"),
]

DATA = pathlib.Path(__file__).parent / "data"
PEP5 = DATA / "pocket_pep5.pdb"

CONFIG = f"""
campaign = "structure-cli"
parent = "ethanol"

[structure]
spec = "{PEP5}"
center = [0.4, -0.1, -0.1]
pocket_cutoff = 12.0

[candidates]
ethanol = "CCO"
propanol = "CCCO"
butanol = "CCCCO"

[poses]
relax_unmapped = true

[docking]
box_size = [22.0, 16.0, 16.0]
seeds = [7]
exhaustiveness = 4
n_poses = 3

[cut]
keep = 1
z = 2.0
floor = 0.0
"""


def _write(tmp_path, text=CONFIG, name="s.toml"):
    p = tmp_path / name
    p.write_text(text)
    return p


def _cli(*args):
    env = {**os.environ, "OPENBLAS_NUM_THREADS": "1"}
    return subprocess.run([sys.executable, "-m", "smeltery", *args], capture_output=True, text=True, env=env)


def _plan(tmp_path, text=CONFIG, out="out", fetch=None):
    from smeltery.cli import build_plan, load_config

    return build_plan(load_config(_write(tmp_path, text)), out_dir=tmp_path / out, fetch=fetch)


def _sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def test_the_prepared_artefacts_are_written_under_out_and_identified_by_sha256(tmp_path):
    plan = _plan(tmp_path)
    st = plan.inputs["structure"]
    sdir = tmp_path / "out" / "structure"
    assert st["artefacts"] == {
        "receptor.pdb": _sha(sdir / "receptor.pdb"),
        "receptor.pdbqt": _sha(sdir / "receptor.pdbqt"),
    }
    assert plan.inputs["docking"]["receptor_sha256"] == st["artefacts"]["receptor.pdbqt"]  # the docked receptor IS it
    assert (
        plan.inputs["pocket"]["input_sha256"] == st["artefacts"]["receptor.pdb"]
    )  # the field comes from the same atoms
    assert plan.inputs["docking"]["box"]["center"] == [0.4, -0.1, -0.1] == st["center"]["xyz"]
    assert "field" not in plan.inputs
    assert [e["stage"] for e in plan.stage_log] == ["structure", "docking", "poses", "pocket"]


def test_the_record_says_experimental_and_carries_the_source_and_the_cutoff(tmp_path):
    plan = _plan(tmp_path)
    st = plan.inputs["structure"]
    assert st["kind"] == "EXPERIMENTAL" == st["structure"]["kind"]
    assert st["structure"]["source"] == "file:pocket_pep5.pdb"
    assert st["structure"]["sha256"] == _sha(PEP5)
    assert st["pocket_cutoff_ang"] == 12.0 == plan.inputs["pocket"]["cutoff_ang"]
    assert plan.inputs["pocket"]["center_ang"] == (0.4, -0.1, -0.1)
    assert st["selection"]["n_protein_atoms"] == 41 and st["selection"]["hetatm_dropped"] == {}
    frame = st["frame"]
    assert frame["receptor_atoms_dropped_by_meeko"] == 0 and frame["dropped_inside_box"] == 0
    assert frame["pdbqt_heavy_atoms_vs_pdb_max_dev_ang"] <= 0.01 and frame["field_covers_box_faces"] is True
    # half-diagonal of 22 x 16 x 16 is 15.8 A > 12 A: the corners are NOT covered, and the record says so
    assert frame["field_covers_box_corners"] is False


def test_a_structure_plan_is_reproduced_by_a_rerun_in_another_directory(tmp_path):
    from smeltery.record import RunRecord

    def digest(plan):
        return RunRecord("c", plan.inputs, plan.tiers, {}).input_digest

    a, b = _plan(tmp_path, out="one"), _plan(tmp_path, out="two")
    assert digest(a) == digest(b)  # the output directory is not an input


KNOBS = [
    ("pocket_cutoff = 12.0", "pocket_cutoff = 13.0"),
    ("center = [0.4, -0.1, -0.1]", "center = [0.5, -0.1, -0.1]"),
    ('spec = "{pep5}"', 'spec = "{pep5}"\nchains = ["A"]'),  # same atoms, but the selection is a knob
    ("[structure]", "[structure]\nallow_network = true"),
    ("box_size = [22.0, 16.0, 16.0]", "box_size = [22.0, 16.0, 15.0]"),
    ("seeds = [7]", "seeds = [8]"),
    ("exhaustiveness = 4", "exhaustiveness = 8"),
]


def test_changing_any_structure_knob_changes_the_digest(tmp_path):
    from smeltery.record import RunRecord

    def digest(text):
        plan = _plan(tmp_path, text)
        return RunRecord("c", plan.inputs, plan.tiers, {}).input_digest

    base = digest(CONFIG)
    for old, new in KNOBS:
        old, new = old.replace("{pep5}", str(PEP5)), new.replace("{pep5}", str(PEP5))
        assert old in CONFIG, old
        assert digest(CONFIG.replace(old, new, 1)) != base, f"{old!r} -> {new!r} is accepted but not in the digest"


def test_the_pdb_id_path_needs_the_opt_in_and_then_uses_the_injected_fetch(tmp_path):
    from smeltery.cli import ConfigError

    text = CONFIG.replace(f'spec = "{PEP5}"', 'spec = "1abc"')
    with pytest.raises(ConfigError, match="allow_network"):
        _plan(tmp_path, text, fetch=lambda url: pytest.fail("network touched"))
    seen = []

    def fetch(url):
        seen.append(url)
        return PEP5.read_bytes()

    plan = _plan(tmp_path, text.replace("[structure]", "[structure]\nallow_network = true"), fetch=fetch)
    assert seen == ["https://files.rcsb.org/download/1ABC.pdb"]
    assert plan.inputs["structure"]["structure"]["source"] == seen[0]
    assert plan.inputs["structure"]["allow_network"] is True


def test_a_reference_ligand_sets_the_centre_and_leaves_the_receptor_without_it(tmp_path):
    ligand = [  # three heavy atoms around (0.4, -0.1, -0.1)
        "HETATM  901  C1  LIG A 301       0.000  -0.100  -0.100  1.00  0.00           C  ",
        "HETATM  902  C2  LIG A 301       0.400  -0.100  -0.100  1.00  0.00           C  ",
        "HETATM  903  O1  LIG A 301       0.800  -0.100  -0.100  1.00  0.00           O  ",
    ]
    pdb = tmp_path / "with_ligand.pdb"
    pdb.write_text(PEP5.read_text().rstrip("\n") + "\n" + "\n".join(ligand) + "\nEND\n")
    text = CONFIG.replace(f'spec = "{PEP5}"', f'spec = "{pdb}"').replace(
        "center = [0.4, -0.1, -0.1]", 'reference_ligand = "LIG"'
    )
    plan = _plan(tmp_path, text)
    st = plan.inputs["structure"]
    assert st["center"]["mode"] == "reference_ligand" and st["center"]["residue"] == "LIG:A:301"
    assert np.allclose(st["center"]["xyz"], [0.4, -0.1, -0.1])
    assert st["selection"]["hetatm_dropped"] == {"LIG": 3}
    assert "LIG" not in (tmp_path / "out" / "structure" / "receptor.pdb").read_text()  # not part of the field either


def test_a_centre_away_from_the_receptor_is_a_loud_frame_error(tmp_path):
    from smeltery.cli import StageError

    text = CONFIG.replace("center = [0.4, -0.1, -0.1]", "center = [100.0, 0.0, 0.0]")
    with pytest.raises(StageError, match="not in the receptor's frame"):
        _plan(tmp_path, text)


def test_a_field_that_does_not_cover_the_box_is_a_loud_error(tmp_path):
    from smeltery.cli import StageError

    with pytest.raises(StageError, match="would not even cover the box faces"):
        _plan(tmp_path, CONFIG.replace("pocket_cutoff = 12.0", "pocket_cutoff = 5.0"))


def _shifted_prepare(monkeypatch, shift=None, drop=0):
    """Wrap prepare_receptor so its PDBQT is moved (a frame error) or loses atoms (Meeko dropping residues)."""
    import smeltery.docking as docking

    real = docking.prepare_receptor

    def wrapped(pdb, out):
        path = real(pdb, out)
        lines = path.read_text().splitlines()
        atoms = [i for i, line in enumerate(lines) if line.startswith("ATOM")]
        if drop:
            for i in atoms[:drop]:
                lines[i] = ""
        if shift:
            for i in atoms:
                if lines[i]:
                    x = float(lines[i][30:38]) + shift
                    lines[i] = lines[i][:30] + f"{x:8.3f}" + lines[i][38:]
        path.write_text("\n".join(lines) + "\n")
        return path

    monkeypatch.setattr(docking, "prepare_receptor", wrapped)


def test_a_pdbqt_in_another_frame_than_the_pocket_pdb_is_refused(tmp_path, monkeypatch):
    from smeltery.cli import StageError

    _shifted_prepare(monkeypatch, shift=1.0)
    with pytest.raises(StageError, match="not in one frame"):
        _plan(tmp_path)


def test_meeko_dropping_atoms_inside_the_box_is_refused(tmp_path, monkeypatch):
    from smeltery.cli import StageError

    _shifted_prepare(monkeypatch, drop=3)
    with pytest.raises(StageError, match="dropped 3 receptor atom"):
        _plan(tmp_path)


def test_a_docked_pose_outside_the_box_is_a_frame_error(tmp_path, monkeypatch):
    from smeltery.cli import StageError
    from smeltery.docking import Docking

    real_run = Docking.run

    def run_then_move(self, candidates, ctx):
        real_run(self, candidates, ctx)
        for pose in candidates[0].poses:
            pose.coords_ang[:] = pose.coords_ang + 100.0

    monkeypatch.setattr(Docking, "run", run_then_move)
    with pytest.raises(StageError, match="outside the search box"):
        _plan(tmp_path)


def test_a_predicted_structure_end_to_end_keeps_its_label_licence_and_attribution(tmp_path):
    tiny = (DATA / "afdb_tiny.pdb").read_bytes()

    def fetch(url):
        if "api/prediction" in url:
            return json.dumps(
                [{"pdbUrl": "https://example.invalid/AF-P69905-F1.pdb", "latestVersion": 4, "entryId": "AF-P69905-F1"}]
            ).encode()
        return tiny

    text = (
        CONFIG.replace(f'spec = "{PEP5}"', 'spec = "P69905"')
        .replace("[structure]", '[structure]\nprovider = "afdb"\nallow_network = true')
        .replace("center = [0.4, -0.1, -0.1]", "center = [4.0, 2.0, 0.0]")
        .replace("pocket_cutoff = 12.0", "pocket_cutoff = 13.0")
    )
    plan = _plan(tmp_path, text, fetch=fetch)
    st = plan.inputs["structure"]
    assert st["kind"] == "PREDICTED" and st["structure"]["license_id"] == "CC-BY-4.0"
    assert "CC BY 4.0" in st["structure"]["attribution"] and st["structure"]["mean_plddt"] is not None
    assert st["pocket_plddt"]["n_residues"] >= 1 and st["pocket_plddt"]["min"] <= st["pocket_plddt"]["mean"]


@pytest.mark.needs_ferric
def test_end_to_end_from_a_structure_matches_the_plan_digest_and_reports_per_stage_counts(tmp_path):
    pytest.importorskip("posebusters", reason="needs the posebusters extra")
    cfg = _write(tmp_path, CONFIG + "\n[gates]\nposebusters = true\nforcefield = true\n")
    out = tmp_path / "out"
    planned = _cli("run", str(cfg), "--out", str(out), "--plan")
    assert planned.returncode == 0, planned.stdout + planned.stderr
    digest = planned.stdout.split("input_digest")[1].split()[0]

    r = _cli("run", str(cfg), "--out", str(out))
    assert r.returncode == 0, r.stdout + r.stderr
    (path,) = out.glob("run-*.json")
    rec = json.loads(path.read_text())
    assert rec["input_digest"] == digest
    st = rec["inputs"]["structure"]
    assert st["artefacts"]["receptor.pdbqt"] == _sha(out / "structure" / "receptor.pdbqt")
    assert [e["stage"] for e in rec["results"]["stages"]] == [
        "structure", "docking", "poses", "pocket", "posebusters", "forcefield",
    ]  # fmt: skip
    funnel = rec["results"]["funnel"]
    assert [f["stage"] for f in funnel] == [
        "structure", "docking", "poses", "pocket", "parity", "posebusters", "forcefield", "field_interaction", "cut",
    ]  # fmt: skip
    by = {f["stage"]: f for f in funnel}
    assert by["poses"]["candidates_in"] == by["posebusters"]["candidates_out"] == 3
    assert by["cut"]["candidates_in"] == 2 and by["cut"]["kind"] == "rank"
    assert "candidates in -> out per stage" in r.stdout and "structure: EXPERIMENTAL" in r.stdout
