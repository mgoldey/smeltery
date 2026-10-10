"""`smeltery run`: config handling, loud stage failures, digests and the honest outcome (issue #17).

The plan-only tests need RDKit only. The run tests need ferric and use a tiny system.
"""

import copy
import json
import os
import pathlib
import subprocess
import sys
import tomllib

import pytest

from smeltery.cli import ConfigError, build_plan, format_table, load_config, main, run_plan, validate_config
from smeltery.record import RunRecord

BASE = """
campaign = "t"
parent = "benzoic"

[candidates]
benzoic = "OC(=O)c1ccccc1"
4-F = "OC(=O)c1ccc(F)cc1"
4-Cl = "OC(=O)c1ccc(Cl)cc1"

[poses]
n_poses = 3

[[pocket.site]]
q = 1.0
atom = 0
from_atom = 1
distance = 3.0

[[pocket.site]]
q = 1.0
atom = 2
from_atom = 1
distance = 3.0

[cut]
keep = 1
z = 2.0
floor = 0.0
"""

SMALL = """
campaign = "small"
parent = "ethanol"

[candidates]
ethanol = "CCO"
propanol = "CCCO"
butanol = "CCCCO"

[poses]
n_poses = 3

[[pocket.site]]
q = 1.0
atom = 2
from_atom = 1
distance = 3.0

[cut]
keep = 1
z = 2.0
floor = {floor}
"""


def write(tmp_path, text, name="c.toml"):
    p = tmp_path / name
    p.write_text(text)
    return p


def cfg_of(text=BASE):
    return tomllib.loads(text)


def digest(cfg):
    plan = build_plan(cfg)
    return RunRecord(cfg["campaign"], plan.inputs, plan.tiers, {}).input_digest


def test_the_same_config_gives_the_same_digest():
    assert digest(cfg_of()) == digest(cfg_of())


# Every knob the config accepts. Changing any one must change the digest.
KNOBS = [
    ("poses", "n_poses", 4),
    ("poses", "seed", 7),
    ("poses", "jitter_deg", 10.0),
    ("poses", "jitter_ang", 0.25),
    ("poses", "min_core_heavy", 4),
    ("poses", "mcs_timeout_s", 20),
    ("poses", "relax_unmapped", True),
    ("field_interaction", "basis", "3-21g"),
    ("field_interaction", "energy_conv", 1e-9),
    ("field_interaction", "density_conv", 1e-7),
    ("cut", "keep", 2),
    ("cut", "z", 3.0),
    ("cut", "floor", 0.5),
]


@pytest.mark.parametrize(("section", "key", "value"), KNOBS)
def test_changing_any_knob_changes_the_digest(section, key, value):
    base = cfg_of()
    changed = copy.deepcopy(base)
    changed.setdefault(section, {})[key] = value
    assert digest(changed) != digest(base), f"[{section}] {key} is accepted but not in the digest"


def test_changing_a_candidate_or_a_pocket_site_changes_the_digest():
    base = cfg_of()
    smiles = copy.deepcopy(base)
    smiles["candidates"]["4-Cl"] = "OC(=O)c1ccc(Br)cc1"
    site = copy.deepcopy(base)
    site["pocket"]["site"][0]["distance"] = 3.5
    charge = copy.deepcopy(base)
    charge["pocket"]["site"][1]["q"] = 0.5
    assert len({digest(base), digest(smiles), digest(site), digest(charge)}) == 4


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda c: c.update(typo=1), "unknown key"),
        (lambda c: c["poses"].update(n_pose=3), "unknown key"),
        (lambda c: c["cut"].update(keeep=1), "unknown key"),
        (lambda c: c.update(parent="nope"), "not listed"),
        (lambda c: c["candidates"].update({"bad": "not a smiles ((("}), "unparseable"),
        (lambda c: c["cut"].update(keep=3), "keep must be"),
        (lambda c: c["poses"].update(scaffold_maps={}), "unknown key"),
        (lambda c: c.update(field_interaction={"field_provenance": {"x": 1}}), "unknown key"),
        (lambda c: c.update(docking={"box_center": [0, 0, 0]}), "missing 'receptor'"),
        (lambda c: c.update(docking={"receptor": "r.pdbqt"}), "missing 'box_center'"),
        (lambda c: c.update(docking={"receptor": "r.pdbqt", "box_center": [0, 0]}), "three numbers"),
        (lambda c: c.update(docking={"receptor": "r.pdbqt", "box_center": [0, 0, 0], "seeds": []}), "seeds"),
        (lambda c: c.update(docking={"receptor": "r.pdbqt", "box_center": [0, 0, 0], "speed": 3}), "unknown key"),
        (
            lambda c: c.update(docking={"receptor": "r.pdbqt", "box_center": [0, 0, 0]}),
            r"\['n_poses'\] have no effect",  # BASE sets [poses] n_poses; with docking it would be silently ignored
        ),
        (lambda c: c.update(gates={"posebusters": "yes"}), "must be true or false"),
        (lambda c: c.update(gates={"magic": True}), "unknown key"),
        (lambda c: c["pocket"].update(file="x.pdb"), "either file or"),
        (lambda c: (c["pocket"].pop("site"), c["pocket"].update(file="x.txt")), r"\.pdb or \.pqr"),
        (lambda c: c["pocket"].update(site=[]), "at least one charge"),
        (lambda c: c["pocket"]["site"][0].pop("distance"), "missing"),
    ],
)
def test_a_bad_config_is_a_config_error_not_a_silent_default(mutate, message):
    cfg = cfg_of()
    mutate(cfg)
    with pytest.raises(ConfigError, match=message):
        validate_config(cfg)


def test_the_example_config_is_valid_and_planned():
    plan = build_plan(load_config(pathlib.Path(__file__).parents[1] / "examples" / "mwe_benzoic.toml"))
    assert [c.name for c in plan.candidates] == ["benzoic", "4-F", "4-Cl"] and len(plan.field) == 2


def test_a_site_atom_outside_the_parent_is_a_config_error():
    cfg = cfg_of()
    cfg["pocket"]["site"][0]["atom"] = 99
    with pytest.raises(ConfigError, match="distinct indices"):
        build_plan(validate_config(cfg))


def test_an_odd_electron_candidate_is_a_loud_parity_error_naming_the_stage(tmp_path, capsys):
    # C2H5O is a radical: 25 electrons, so closed-shell RHF is undefined.
    p = tmp_path / "radical.toml"
    p.write_text(SMALL.format(floor=0.0).replace('butanol = "CCCCO"', 'radical = "C[CH]O"'))
    assert main(["run", str(p), "--plan", "--out", str(tmp_path)]) == 3
    err = capsys.readouterr().err
    assert "parity" in err and "radical" in err and "odd electron count" in err


def test_a_config_error_exits_2_and_a_missing_floor_is_explained(tmp_path, capsys):
    p = tmp_path / "nofloor.toml"
    p.write_text(BASE.replace("floor = 0.0\n", ""))
    assert main(["run", str(p), "--plan"]) == 2
    err = capsys.readouterr().err
    assert "config error" in err and "[cut] floor" in err and "not measured" in err
    assert main(["run", str(tmp_path / "missing.toml"), "--plan"]) == 2


def test_no_common_core_is_a_loud_stage_error(tmp_path, capsys):
    p = tmp_path / "nocore.toml"
    p.write_text(SMALL.format(floor=0.0).replace('butanol = "CCCCO"', 'benzene = "c1ccccc1"'))
    assert main(["run", str(p), "--plan"]) == 3
    assert "stage 'poses' failed" in capsys.readouterr().err


def test_plan_mode_runs_no_scf_and_prints_the_digest_and_atom_indices(tmp_path, capsys):
    p = tmp_path / "c.toml"
    p.write_text(BASE)
    assert main(["run", str(p), "--plan"]) == 0
    out = capsys.readouterr().out
    assert "no SCF run" in out and "0:O, 1:C, 2:O" in out and "input_digest" in out
    assert not list(tmp_path.glob("run-*.json"))


@pytest.mark.needs_ferric
def test_an_unranked_run_exits_zero_and_a_rerun_reproduces_the_digest_and_the_decision(tmp_path, capsys):
    p = tmp_path / "wide.toml"
    p.write_text(SMALL.format(floor=1000.0))
    records = []
    for i in range(2):
        out_dir = tmp_path / f"run{i}"
        assert main(["run", str(p), "--out", str(out_dir)]) == 0
        printed = capsys.readouterr().out
        assert "UNRANKED" in printed and "propanol, butanol" in printed.replace("] | [", "")  # one tie group
        (path,) = out_dir.glob("run-*.json")
        records.append(json.loads(path.read_text()))
    a, b = records
    assert a["input_digest"] == b["input_digest"]
    assert a["results"] == b["results"]  # same ranking decision AND the same numbers
    assert a["results"]["cut"]["unranked"] is True and a["results"]["cut"]["survivors"] is None


@pytest.mark.needs_ferric
def test_the_cli_adds_no_arithmetic_it_equals_the_library_called_by_hand():
    from smeltery import Candidate, PointCharge, paired_delta
    from smeltery.tiers import FieldInteraction, PairedPoses

    cfg = validate_config(tomllib.loads(SMALL.format(floor=0.0)))
    plan = build_plan(cfg)
    _, out = run_plan(plan)

    cands = [Candidate(n, s) for n, s in cfg["candidates"].items()]
    parent = cands[0]
    PairedPoses(n_poses=3).run(cands, {"parent": parent})
    xyz = parent.poses[0].coords_ang
    u = xyz[2] - xyz[1]
    field = [PointCharge(1.0, tuple(float(v) for v in xyz[2] + 3.0 * u / (u @ u) ** 0.5))]
    FieldInteraction().run(cands, {"field": field})
    for c in cands[1:]:
        by_hand = paired_delta(parent, c, "dE_int")
        assert out["paired"][c.name].mean == pytest.approx(by_hand.mean, abs=1e-12)
        assert out["paired"][c.name].sem == pytest.approx(by_hand.sem, abs=1e-12)


class _KnownValues:
    """Stands in for the SCF tier: writes fixed per-pose dE_int, so only the cut's floor can decide the outcome."""

    def __init__(self, real, values):
        self._real, self._values = real, values
        self.name = real.name

    def run(self, candidates, ctx):
        for c in candidates:
            c.per_pose["dE_int"] = list(self._values[c.name])

    def __getattr__(self, item):
        return getattr(self._real, item)


@pytest.mark.parametrize(("floor", "ranked"), [(0.0, True), (5.0, False)])
def test_the_configured_floor_alone_decides_whether_a_resolved_difference_is_ranked(floor, ranked):
    cfg = validate_config(tomllib.loads(SMALL.format(floor=floor)))
    plan = build_plan(cfg)
    plan.tier = _KnownValues(
        plan.tier, {"ethanol": [0.0, 0.0, 0.0], "propanol": [1.0, 1.1, 0.9], "butanol": [3.0, 3.1, 2.9]}
    )
    _, out = run_plan(plan)
    # SEMs are ~0.06 and the means differ by 2.0: resolved by statistics at any floor below 2.
    assert out["cut"].unranked_at_boundary is (not ranked)
    assert (out["cut"].survivors == ["propanol"]) is ranked
    text = format_table(plan, out)
    assert (f"floor {floor:g} kcal/mol, from config" in text) and (("UNRANKED" in text) is (not ranked))


GATED = SMALL.format(floor=0.0) + "\n[gates]\nposebusters = true\n"


def test_gates_change_the_digest():
    base = cfg_of(SMALL.format(floor=0.0))
    gated = copy.deepcopy(base)
    gated["gates"] = {"forcefield": True}
    assert digest(gated) != digest(base)
    both = copy.deepcopy(base)
    both["gates"] = {"forcefield": True, "xtb": True}
    assert len({digest(base), digest(gated), digest(both)}) == 3


def test_the_forcefield_gate_records_strain_per_candidate(tmp_path, capsys):
    p = tmp_path / "ff.toml"
    p.write_text(SMALL.format(floor=0.0) + "\n[gates]\nforcefield = true\n")
    plan = build_plan(load_config(p))
    assert [e["stage"] for e in plan.stage_log] == ["poses", "pocket", "forcefield"]
    strain = plan.stage_log[-1]["strain"]
    assert set(strain) == {"ethanol", "propanol", "butanol"} and all(len(v) == 3 for v in strain.values())
    assert [t["name"] for t in plan.tiers] == ["paired_poses", "forcefield", "field_interaction"]


def _cli(*args):
    """The CLI as a real subprocess.

    PoseBusters tests cannot run in-process after pytest has swapped `sys.stderr`: RDKit's Python log stream is
    then a closed file, and PoseBusters checks fail with "I/O operation on closed file" (reported as pose
    failures, so it fails closed, but it makes the outcome depend on test order).
    """
    env = {**os.environ, "OPENBLAS_NUM_THREADS": "1"}
    return subprocess.run([sys.executable, "-m", "smeltery", *args], capture_output=True, text=True, env=env)


def test_posebusters_rejects_unrelaxed_analogue_poses_loudly_with_the_reason(tmp_path):
    pytest.importorskip("posebusters", reason="needs the posebusters extra")
    p = tmp_path / "pb.toml"
    p.write_text(GATED)
    r = _cli("run", str(p), "--plan")
    assert r.returncode == 3, r.stdout + r.stderr
    assert "stage 'posebusters' failed" in r.stderr and "propanol" in r.stderr and "relax_unmapped = true" in r.stderr


def test_relaxing_only_the_substituent_lets_the_same_poses_pass_posebusters(tmp_path):
    pytest.importorskip("posebusters", reason="needs the posebusters extra")
    p = tmp_path / "pb_relax.toml"
    p.write_text(GATED.replace("n_poses = 3", "n_poses = 3\nrelax_unmapped = true"))
    r = _cli("run", str(p), "--plan")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "no SCF run" in r.stdout and "posebusters" in r.stdout


def test_a_pocket_file_that_does_not_exist_is_a_config_error(tmp_path, capsys):
    p = tmp_path / "nofile.toml"
    cfg_text = SMALL.format(floor=0.0).replace(
        "[[pocket.site]]\nq = 1.0\natom = 2\nfrom_atom = 1\ndistance = 3.0\n", ""
    )
    p.write_text(cfg_text.replace("[cut]", '[pocket]\nfile = "missing.pdb"\n\n[cut]'))
    assert main(["run", str(p), "--plan"]) == 2
    assert "does not exist" in capsys.readouterr().err
