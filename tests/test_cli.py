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
relax_unmapped = true

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
        (tie_line,) = [ln for ln in printed.splitlines() if ln.startswith("tie groups")]
        assert "UNRANKED" in printed and sorted(tie_line.split(": ", 1)[1].split(", ")) == [
            "butanol",
            "propanol",
        ]  # one tie group (order within a group is arbitrary)
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
    PairedPoses(n_poses=3, relax_unmapped=True).run(cands, {"parent": parent})
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
GATED_UNRELAXED = GATED.replace("relax_unmapped = true\n", "")


def test_gates_change_the_digest():
    base = cfg_of(SMALL.format(floor=0.0))
    gated = copy.deepcopy(base)
    gated["gates"] = {"forcefield": True}
    assert digest(gated) != digest(base)
    # (the xtb and posebusters toggles reach the digest the same way, through plan.inputs["gates"]; building a plan
    # with them on would run xtb / PoseBusters, which this test does not need)
    toggled = copy.deepcopy(gated)
    toggled["gates"] = {"forcefield": False}
    assert digest(toggled) == digest(base)  # an explicit false is the same as absent
    assert digest(gated) != digest(base)


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
    p.write_text(GATED_UNRELAXED)
    r = _cli("run", str(p), "--plan")
    assert r.returncode == 3, r.stdout + r.stderr
    assert "stage 'posebusters' failed" in r.stderr and "propanol" in r.stderr and "relax_unmapped = true" in r.stderr


def test_relaxing_only_the_substituent_lets_the_same_poses_pass_posebusters(tmp_path):
    pytest.importorskip("posebusters", reason="needs the posebusters extra")
    p = tmp_path / "pb_relax.toml"
    p.write_text(GATED)
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


# ---- choosing the measurement: [field_dft_interaction] selects KS-DFT instead of RHF -----------------------------

PBE = SMALL.format(floor=0.0) + '\n[field_dft_interaction]\nfunctional = "PBE"\n'


def test_a_field_dft_interaction_section_measures_with_ks_dft_and_says_so_in_the_record():
    from smeltery.tiers import FieldDftInteraction, FieldInteraction

    rhf = build_plan(validate_config(tomllib.loads(SMALL.format(floor=0.0))))
    pbe = build_plan(validate_config(tomllib.loads(PBE)))
    assert type(rhf.tier) is FieldInteraction and type(pbe.tier) is FieldDftInteraction
    (rec,) = [t for t in pbe.tiers if t["name"] == "field_dft_interaction"]
    assert rec["method"] == "KS-DFT PBE + D3(BJ)" and rec["functional"] == "PBE"
    assert "field_interaction" not in [t["name"] for t in pbe.tiers]
    assert digest(validate_config(tomllib.loads(PBE))) != digest(
        validate_config(tomllib.loads(SMALL.format(floor=0.0)))
    )


def test_rhf_and_ks_dft_together_is_a_config_error_not_a_silent_choice():
    both = PBE + '\n[field_interaction]\nbasis = "sto-3g"\n'
    with pytest.raises(ConfigError, match="keep one"):
        validate_config(tomllib.loads(both))


def test_an_unknown_ks_dft_knob_is_a_config_error():
    with pytest.raises(ConfigError, match=r"\[field_dft_interaction\]"):
        validate_config(tomllib.loads(PBE + "xc = 'PBE0'\n"))


@pytest.mark.parametrize(
    ("key", "value"),
    [("functional", "B3LYP"), ("basis", "3-21g"), ("energy_conv", 1e-9), ("memory_budget_gb", 16.0)],
)
def test_changing_any_ks_dft_knob_changes_the_digest(key, value):
    base = validate_config(tomllib.loads(PBE))
    changed = copy.deepcopy(base)
    changed["field_dft_interaction"][key] = value
    assert digest(changed) != digest(base), f"[field_dft_interaction] {key} is accepted but not in the digest"


def test_the_funnel_and_the_stage_errors_name_the_tier_that_measured():
    plan = build_plan(validate_config(tomllib.loads(PBE)))
    plan.tier = _KnownValues(
        plan.tier, {"ethanol": [0.0, 0.0, 0.0], "propanol": [1.0, 1.1, 0.9], "butanol": [3.0, 3.1, 2.9]}
    )
    rec, out = run_plan(plan)
    measure = [f for f in rec.results["funnel"] if f["kind"] == "measure"]
    assert [f["stage"] for f in measure] == ["field_dft_interaction"]
    text = format_table(plan, out)
    assert "-> field_dft_interaction" in text
    # the per-stage table stays a table: every row's kind starts in the same column
    rows = [ln for ln in text.splitlines() if ln.startswith("  ") and " -> " in ln]
    kinds = {ln.index(f" {k} ") for ln in rows for k in ("pair", "field", "gate", "measure", "rank") if f" {k} " in ln}
    assert len(rows) >= 4 and len(kinds) == 1, rows


@pytest.mark.needs_ferric
def test_a_pbe_run_through_the_cli_equals_the_pbe_tier_called_by_hand(tmp_path):
    """Data source: FieldDftInteraction run directly on the same poses and field, outside the CLI."""
    from smeltery import Candidate, PointCharge, paired_delta
    from smeltery.tiers import FieldDftInteraction, PairedPoses

    cfg = validate_config(tomllib.loads(PBE))
    plan = build_plan(cfg)
    rec, out = run_plan(plan)
    assert set(rec.results["units"]) >= {"dE_int", "E_d3"}  # D3(BJ) is recorded per pose, beside dE_int

    cands = [Candidate(n, s) for n, s in cfg["candidates"].items()]
    parent = cands[0]
    PairedPoses(n_poses=3, relax_unmapped=True).run(cands, {"parent": parent})
    xyz = parent.poses[0].coords_ang
    u = xyz[2] - xyz[1]
    field = [PointCharge(1.0, tuple(float(v) for v in xyz[2] + 3.0 * u / (u @ u) ** 0.5))]
    FieldDftInteraction(functional="PBE").run(cands, {"field": field})
    for c in cands[1:]:
        by_hand = paired_delta(parent, c, "dE_int")
        assert out["paired"][c.name].mean == pytest.approx(by_hand.mean, abs=1e-9)


# ---- --plan says what memory the measurement needs, before any SCF ---------------------------------------------

BENZOIC_PBE = BASE + '\n[field_dft_interaction]\nfunctional = "PBE"\n'


def _plan_text(text, tmp_path, capsys):
    assert main(["run", str(write(tmp_path, text)), "--plan"]) == 0
    return capsys.readouterr().out


def test_plan_prints_the_tiers_own_memory_prediction_for_the_largest_pose(tmp_path, capsys):
    """Data source: FieldDftInteraction.estimate_memory on the same plan, the call its pre-SCF check makes."""
    out = _plan_text(BENZOIC_PBE, tmp_path, capsys)
    plan = build_plan(validate_config(tomllib.loads(BENZOIC_PBE)))
    mem = plan.tier.estimate_memory(plan.candidates)
    (line,) = [ln for ln in out.splitlines() if ln.startswith("memory (field_dft_interaction")]
    assert f"{mem['n_atoms']} atoms, {mem['n_basis_functions']} basis functions" in line
    assert f"needs {mem['total_gb']:.2f} GB" in line and line.endswith(": fits")
    assert any(ln.startswith("machine: ") for ln in out.splitlines())


def test_plan_says_when_the_run_would_refuse_and_when_it_would_not_check(tmp_path, capsys):
    tight = BENZOIC_PBE + "memory_budget_gb = 0.001\n"
    assert "does NOT fit: the run will refuse before its first SCF" in _plan_text(tight, tmp_path, capsys)
    unchecked = tight + "check_memory = false\n"
    assert "check_memory = false, so the run will start anyway" in _plan_text(unchecked, tmp_path, capsys)


def test_plan_says_cannot_say_for_a_basis_the_model_cannot_count_never_fits(tmp_path, capsys):
    out = _plan_text(
        BENZOIC_PBE.replace('functional = "PBE"\n', 'functional = "PBE"\nbasis = "def2-svp"\n'), tmp_path, capsys
    )
    (line,) = [ln for ln in out.splitlines() if ln.startswith("memory (")]
    assert "cannot say" in line and "def2-svp" in line and "fits" not in line


def test_plan_compares_free_memory_with_require_free_gb(tmp_path, capsys, monkeypatch):
    import smeltery.cli as cli_mod

    monkeypatch.setattr(cli_mod, "available_memory_gb", lambda: 3.0)  # data source: a machine with 3 GB free
    out = _plan_text(BENZOIC_PBE + "require_free_gb = 12.0\n", tmp_path, capsys)
    assert "3.0 GB available now, below require_free_gb 12: the run would refuse if started now" in out
    monkeypatch.setattr(cli_mod, "available_memory_gb", lambda: None)
    assert "unknown (no /proc/meminfo)" in _plan_text(BENZOIC_PBE, tmp_path, capsys)


def test_an_rhf_plan_says_it_has_no_memory_model_rather_than_printing_nothing(tmp_path, capsys):
    out = _plan_text(BASE, tmp_path, capsys)
    assert "memory: field_interaction has no memory model; nothing is checked before its SCFs" in out
