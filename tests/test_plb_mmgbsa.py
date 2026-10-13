# ruff: noqa: E501
"""The PLB MM/GBSA baseline (issue #27): the energy arithmetic, the analysis on synthetic data (with the mutations that
must fail it), and the committed measurements recomputed from file (floats to rel 1e-12, never bit for bit).

The OpenMM tests need OpenMM only (two point particles, no ligand parameterisation) and skip without it; nothing here runs
the real protein/ligand calculation, which ran once in the optional conda environment (docs/benchmarks/plb_mmgbsa_plan.md).
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

from smeltery.benchmark import load_benchmark

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "benchmarks" / "plb" / "results"
REPORT = ROOT / "docs" / "benchmarks" / "plb_mmgbsa.md"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def tool():
    return _load("plb_mmgbsa")


@pytest.fixture(scope="module")
def ana():
    return _load("analyse_plb_mmgbsa")


@pytest.fixture
def fast(ana, monkeypatch):
    """The analysis module with few bootstrap resamples (the synthetic tests do not need 10,000)."""
    monkeypatch.setattr(ana, "N_BOOT", 300)
    return ana


def _same(a, b, rel=1e-12):
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k], rel) for k in a)
    if isinstance(a, (list, tuple)):
        return len(a) == len(b) and all(_same(x, y, rel) for x, y in zip(a, b))
    if isinstance(a, float) or isinstance(b, float):
        if a is None or b is None:
            return a is b
        return bool(np.isclose(a, b, rtol=rel, atol=1e-12)) or (np.isnan(a) and np.isnan(b))
    return a == b


# ------------------------------------------------------------------------------------------------ arithmetic


def test_delta_terms_is_complex_minus_receptor_minus_ligand(tool):
    c = {"bonded": 100.0, "vdw": -50.0, "elec": -30.0, "gbsa": 7.0}
    r = {"bonded": 60.0, "vdw": -40.0, "elec": -10.0, "gbsa": 20.0}
    lg = {"bonded": 40.0, "vdw": -2.0, "elec": -5.0, "gbsa": -9.0}
    d = tool.delta_terms(c, r, lg)
    assert d == {"bonded": 0.0, "vdw": -8.0, "elec": -15.0, "gbsa": -4.0, "total": -27.0}


def test_relative_score_is_analogue_minus_parent(tool):
    out = tool.relative_scores({"p": -10.0, "a": -12.5, "b": -7.0}, "p")
    assert out == {"p": 0.0, "a": -2.5, "b": 3.0}
    with pytest.raises(tool.MmgbsaError):
        tool.relative_scores({"a": 1.0}, "p")


def test_shell_keeps_a_residue_with_any_atom_within_the_radius_and_drops_the_rest(tool):
    lig = np.array([[0.0, 0.0, 0.0], [5.0, 0.0, 0.0]])
    res = {
        "near": np.array([[0.0, 0.0, 30.0], [5.0, 9.99, 0.0]]),  # one atom 9.99 A from the second ligand atom
        "edge": np.array([[0.0, 10.01, 0.0]]),
        "far": np.array([[0.0, 0.0, 17.0], [0.0, 0.0, 20.0]]),
    }
    kept = tool.residues_in_shell(res, lig, 10.0)
    assert set(kept) == {"near"}
    assert kept["near"] == pytest.approx(9.99, rel=1e-12)
    assert set(tool.residues_in_shell(res, lig, 10.5)) == {"near", "edge"}


# ------------------------------------------------------------------------------------------------ OpenMM energy terms


def _two_particle_system(q1, q2, sig, eps):
    import openmm

    sys_ = openmm.System()
    nb = openmm.NonbondedForce()
    nb.setNonbondedMethod(openmm.NonbondedForce.NoCutoff)
    for q in (q1, q2):
        sys_.addParticle(12.0)
        nb.addParticle(q, sig, eps)
    sys_.addForce(nb)
    return sys_


def test_energy_terms_split_vdw_from_coulomb_against_the_analytic_values(tool):
    pytest.importorskip("openmm")
    pytest.importorskip("openmmforcefields")
    q1, q2, sig, eps, r = 0.5, -0.8, 0.3, 0.7, 0.45  # e, e, nm, kJ/mol, nm
    t = tool.energy_terms(_two_particle_system(q1, q2, sig, eps), np.array([[0.0, 0.0, 0.0], [r, 0.0, 0.0]]))
    coulomb_kj = 138.93545764438198 * q1 * q2 / r
    lj_kj = 4 * eps * ((sig / r) ** 12 - (sig / r) ** 6)
    assert t["elec"] == pytest.approx(coulomb_kj / 4.184, rel=1e-9)
    assert t["vdw"] == pytest.approx(lj_kj / 4.184, rel=1e-9)
    assert t["bonded"] == 0.0 and t["gbsa"] == 0.0


def test_energy_terms_convert_the_bonded_energy_to_kcal(tool):
    openmm = pytest.importorskip("openmm")
    pytest.importorskip("openmmforcefields")
    system = _two_particle_system(0.0, 0.0, 0.3, 0.0)
    bond = openmm.HarmonicBondForce()
    bond.addBond(0, 1, 0.40, 2000.0)  # r0 nm, k kJ/mol/nm^2
    system.addForce(bond)
    t = tool.energy_terms(system, np.array([[0.0, 0.0, 0.0], [0.45, 0.0, 0.0]]))
    assert t["bonded"] == pytest.approx(0.5 * 2000.0 * 0.05**2 / 4.184, rel=1e-9)


def test_two_distant_particles_have_no_binding_energy(tool):
    pytest.importorskip("openmm")
    pytest.importorskip("openmmforcefields")
    import openmm

    s2 = _two_particle_system(0.5, -0.8, 0.3, 0.7)
    far = np.array([[0.0, 0.0, 0.0], [1.0e5, 0.0, 0.0]])
    near = np.array([[0.0, 0.0, 0.0], [0.45, 0.0, 0.0]])
    one = lambda q: _one_particle_system(openmm, q)  # noqa: E731
    e_far = tool.delta_terms(
        tool.energy_terms(s2, far), tool.energy_terms(one(0.5), far[:1]), tool.energy_terms(one(-0.8), far[1:])
    )
    e_near = tool.delta_terms(
        tool.energy_terms(s2, near), tool.energy_terms(one(0.5), near[:1]), tool.energy_terms(one(-0.8), near[1:])
    )
    assert abs(e_far["total"]) < 1e-3
    assert e_near["elec"] == pytest.approx(
        138.93545764438198 * 0.5 * -0.8 / 0.45 / 4.184, rel=1e-9
    )  # attraction: C - R - L < 0


def test_native_gb_force_equals_the_xml_custom_gb_force_on_a_fixture_peptide(tool):
    """The amendment A2 claim: add_gb() is the same OBC2 model as implicit/obc2.xml (on a real 6-residue fixture)."""
    openmm = pytest.importorskip("openmm")
    pytest.importorskip("openmmforcefields")
    from openmm import app, unit

    pdb = app.PDBFile(str(ROOT / "tests" / "data" / "plb" / "mini_cdk2.pdb"))
    mod = app.Modeller(pdb.topology, pdb.positions)
    mod.delete([r for r in mod.topology.residues() if r.name in ("HOH", "TPO")])
    pos = np.asarray(mod.positions.value_in_unit(unit.nanometer))
    native = tool.create_system(app.ForceField("amber14-all.xml"), mod.topology)
    xml = app.ForceField("amber14-all.xml", "implicit/obc2.xml").createSystem(
        mod.topology, nonbondedMethod=app.NoCutoff, constraints=None, ignoreExternalBonds=True
    )
    ref = openmm.Platform.getPlatformByName("Reference")

    def gb_energy(system):
        for f in system.getForces():
            f.setForceGroup(2 if type(f).__name__ in ("CustomGBForce", "GBSAOBCForce") else 0)
        ctx = openmm.Context(system, openmm.VerletIntegrator(0.001), ref)
        ctx.setPositions(pos)
        return ctx.getState(getEnergy=True, groups={2}).getPotentialEnergy().value_in_unit(unit.kilojoule_per_mole)

    assert gb_energy(native) == pytest.approx(gb_energy(xml), rel=1e-4)
    assert abs(gb_energy(native)) > 1.0


def _one_particle_system(openmm, q):
    s = openmm.System()
    nb = openmm.NonbondedForce()
    s.addParticle(12.0)
    nb.addParticle(q, 0.3, 0.7)
    s.addForce(nb)
    return s


# ------------------------------------------------------------------------------------------------ analysis on synthetic data


def _fake(ddg: dict[str, float], dg_by_variant: dict[str, dict[str, float]], ref="p"):
    ligs = {}
    for v, dgs in dg_by_variant.items():
        for n, val in dgs.items():
            ligs.setdefault(n, {})[v] = {
                "dG": {"total": val, "vdw": val / 2, "elec": val / 4, "gbsa": val / 4, "bonded": 0.0},
                "minimisation": {"converged": True},
            }
    res = {"target": "t", "reference": ref, "variants": list(dg_by_variant), "ligands": ligs}
    names = sorted(n for n in ddg if n != ref)
    ddE_an = {
        "exp_ddg": dict(ddg),
        "models": {
            "amber14": {"per_ligand": {n: {"ddE_mean": float(-ddg[n]) + (0.1 * i) % 0.3} for i, n in enumerate(names)}}
        },
        "baselines": {"crippen_logp": {"values": {n: float(i % 3) for i, n in enumerate(names)}}},
    }

    class Lg:
        def __init__(self, name, v):
            self.name, self.ddg_kcal_mol = name, v

    class Tgt:
        reference = ref
        ligands = [Lg(n, v) for n, v in ddg.items()]

    return res, ddE_an, Tgt()


DDG = {"p": 0.0, "a": -1.5, "b": -0.9, "c": -0.2, "d": 0.3, "e": 0.8, "f": 1.1, "g": 1.7}


def test_score_sign_convention_a_tighter_binder_has_a_lower_dG_and_rho_is_positive(fast):
    dgs = {"p": -40.0, **{n: -40.0 + 3.0 * v for n, v in DDG.items() if n != "p"}}  # dG tracks ddG: perfect ranking
    res, ddE_an, tgt = _fake(DDG, {"gaff2": dgs})
    out = fast.analyse(res, ddE_an, tgt)
    pt = out["variants"]["gaff2"]["primary"]["point"]
    assert pt["spearman"] == pytest.approx(1.0, rel=1e-12)
    assert out["variants"]["gaff2"]["primary"]["label"] == "tracks (weakly)"
    assert out["variants"]["gaff2"]["ddG_MM"]["a"] == pytest.approx(-4.5, rel=1e-12)  # analogue minus parent
    # the opposite ranking is anti-correlated, not "tracks"
    rev = {"p": -40.0, **{n: -40.0 - 3.0 * v for n, v in DDG.items() if n != "p"}}
    res2, _, _ = _fake(DDG, {"gaff2": rev})
    o2 = fast.analyse(res2, ddE_an, tgt)["variants"]["gaff2"]["primary"]
    assert o2["point"]["spearman"] == pytest.approx(-1.0, rel=1e-12)
    assert o2["label"] == "anti-correlated"


def test_paired_delta_rho_is_mm_minus_ddE(fast):
    dgs = {"p": -40.0, **{n: -40.0 + 3.0 * v for n, v in DDG.items() if n != "p"}}
    res, ddE_an, tgt = _fake(DDG, {"gaff2": dgs})
    v = fast.analyse(res, ddE_an, tgt)["variants"]["gaff2"]
    rho_ddE = fast.spearman(
        [DDG[n] for n in sorted(DDG) if n != "p"],
        [ddE_an["models"]["amber14"]["per_ligand"][n]["ddE_mean"] for n in sorted(DDG) if n != "p"],
    )
    assert v["vs_ddE"]["delta_spearman"] == pytest.approx(1.0 - rho_ddE, rel=1e-12)
    assert v["vs_ddE"]["delta_spearman"] > 0


def test_a_missing_ligand_is_reported_not_dropped(fast):
    dgs = {"p": -40.0, **{n: -40.0 + 3.0 * v for n, v in DDG.items() if n != "p" and n != "c"}}
    res, ddE_an, tgt = _fake(DDG, {"gaff2": dgs})
    out = fast.analyse(res, ddE_an, tgt)["variants"]["gaff2"]
    assert out["missing_ligands"] == ["c"] and "primary" not in out


def test_extra_ligand_is_descriptive_only(fast):
    ddg = {**DDG, "x": 0.5}
    dgs = {"p": -40.0, **{n: -40.0 + 3.0 * v for n, v in ddg.items() if n != "p"}}
    res, ddE_an, tgt = _fake(ddg, {"gaff2": dgs})
    del ddE_an["models"]["amber14"]["per_ligand"]["x"]  # the ddE study has no value for x
    out = fast.analyse(res, ddE_an, tgt)
    assert out["extra_ligands_descriptive_only"] == ["x"]
    assert out["variants"]["gaff2"]["with_excluded_ligands_descriptive"]["point"]["n"] == 8
    assert out["variants"]["gaff2"]["primary"]["point"]["n"] == 7  # the paired set is the ddE study's


def test_the_manifest_and_the_ddE_study_must_agree_on_experiment(fast):
    dgs = {"p": -40.0, **{n: -40.0 + 3.0 * v for n, v in DDG.items() if n != "p"}}
    res, ddE_an, tgt = _fake(DDG, {"gaff2": dgs})
    ddE_an["exp_ddg"]["a"] += 0.5
    with pytest.raises(ValueError, match="experimental ddG differs"):
        fast.analyse(res, ddE_an, tgt)


def test_comparison_label_reads_only_a_ci_that_excludes_zero(fast):
    assert fast.comparison_label(0.1, 0.9).startswith("MM/GBSA ranks")
    assert fast.comparison_label(-0.9, -0.1).startswith("the comparator ranks")
    assert fast.comparison_label(-0.1, 0.9) == "cannot be distinguished at this n"
    assert fast.comparison_label(None, None) == "cannot be distinguished at this n"


# ------------------------------------------------------------------------------------------------ the committed measurements

CASES = {"cdk2": 8, "tyk2": 12}
HAVE = {t: (RES / f"{t}_mmgbsa.json").exists() for t in CASES}


@pytest.mark.skipif(not any(HAVE.values()), reason="no committed MM/GBSA results yet")
@pytest.mark.parametrize("target", [t for t in CASES if HAVE[t]])
def test_committed_results_are_internally_consistent_and_match_the_manifest(target):
    res = json.loads((RES / f"{target}_mmgbsa.json").read_text())
    bench = load_benchmark().targets[target]
    assert res["schema"] == "smeltery-plb-mmgbsa/1"
    assert res["reference"] == bench.reference
    assert set(res["ligands"]) <= {lg.name for lg in bench.ligands}
    assert res["total_cpu_s"] <= 6.0 * 3600.0
    for per_v in res["ligands"].values():
        for r in per_v.values():
            dg = r["dG"]
            for t in ("vdw", "elec", "gbsa"):
                assert _same(dg[t], r["E_complex"][t] - r["E_receptor"][t] - r["E_ligand"][t])
            assert abs(dg["bonded"]) < 1e-6
            assert _same(dg["total"], dg["bonded"] + dg["vdw"] + dg["elec"] + dg["gbsa"])
            assert r["charges"]["max_deviation_from_system_e"] < 1e-6
            assert abs(r["charges"]["charge_sum_e"] - r["charges"]["formal_charge"]) < 1e-3


@pytest.mark.skipif(not any(HAVE.values()), reason="no committed MM/GBSA results yet")
@pytest.mark.parametrize("target", [t for t in CASES if HAVE[t]])
def test_committed_analysis_recomputes_from_the_results(ana, target):
    res = json.loads((RES / f"{target}_mmgbsa.json").read_text())
    ddE_an = json.loads((RES / f"{target}_sto3g_analysis.json").read_text())
    stored = json.loads((RES / f"{target}_mmgbsa_analysis.json").read_text())
    fresh = json.loads(json.dumps(ana.analyse(res, ddE_an, load_benchmark().targets[target])))
    assert _same(fresh, stored)
    assert len(stored["analogues"]) == CASES[target]
    corrupted = copy.deepcopy(res)
    n = stored["analogues"][0]
    v = res["variants"][0]
    corrupted["ligands"][n][v]["dG"]["total"] += 25.0
    changed = json.loads(json.dumps(ana.analyse(corrupted, ddE_an, load_benchmark().targets[target])))
    assert not _same(changed, stored)


@pytest.mark.skipif(not REPORT.exists(), reason="report not written yet")
@pytest.mark.parametrize("target", [t for t in CASES if HAVE[t]])
def test_the_report_quotes_the_analysis_file(target):
    stored = json.loads((RES / f"{target}_mmgbsa_analysis.json").read_text())
    text = REPORT.read_text()
    p = stored["variants"]["gaff2"]["primary"]["point"]["spearman"]
    assert f"{p:+.3f}" in text, f"the report does not quote the {target} primary rho {p:+.3f}"
    d = stored["variants"]["gaff2"]["vs_ddE"]["delta_spearman"]
    assert f"{d:+.3f}" in text
    assert "plb_mmgbsa_plan.md" in text
