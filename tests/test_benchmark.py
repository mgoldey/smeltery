"""The experimental ddG benchmark (issue #26): provenance, ddG arithmetic, the strict loader, the fetcher."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path

import pytest

from benchmarks.plb import suitability as suit
from smeltery import benchmark as bm

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "benchmarks" / "plb" / "manifest.json"
RT = 0.001987204258640832 * 298.15  # kcal/mol, written independently of the module


@pytest.fixture(scope="module")
def raw() -> dict:
    return json.loads(MANIFEST.read_text())


@pytest.fixture(scope="module")
def bench() -> bm.Benchmark:
    return bm.load_benchmark(MANIFEST)


def _unit_molar(t: str, v: float, u: str) -> float:
    return 10 ** (-v) if t == "pic50" else v * {"M": 1, "mM": 1e-3, "uM": 1e-6, "nM": 1e-9, "pM": 1e-12}[u]


# ---------------------------------------------------------------- the committed data


def test_enough_series(bench):
    big = [t for t in bench.targets.values() if len(t.ligands) - 1 >= 6]
    assert len(big) >= 3
    assert len(bench.targets) == 15


def test_every_entry_has_complete_provenance(bench):
    for t in bench.targets.values():
        assert t.pdb_id and t.assay_type in bm.MEASUREMENT_TYPES and t.protonation.strip()
        assert t.files, t.name
        for f in t.files:
            assert f.url.startswith("https://raw.githubusercontent.com/") and bench.upstream_commit in f.url
            assert len(f.sha256) == 64 and f.size_bytes > 0
        for lig in t.ligands:
            m = lig.measurement
            assert m.type == t.assay_type and m.sources and m.value > 0 and m.unit is not None
            assert lig.smiles and lig.smiles_upstream and lig.name.startswith("lig_")
            for s in m.sources:
                assert s.id.strip() and s.kind in ("doi", "url")


def test_attribution_and_licence_present(bench):
    assert bench.data_licence == "CC-BY-4.0"
    for needle in ("Open Forcefield Group", "CC BY 4.0", bench.upstream_commit, "10.5281/zenodo.4813735", "Changes:"):
        assert needle in bench.attribution
    assert "Copyright" in bench.copyright and bench.code_licence == "MIT"


def test_ddg_recomputes_from_raw_measurement(bench):
    for t in bench.targets.values():
        ref = t.ligand(t.reference)
        xr = _unit_molar(ref.measurement.type, ref.measurement.value, ref.measurement.unit)
        assert ref.ddg_kcal_mol == 0.0
        for lig in t.ligands:
            m = lig.measurement
            x = _unit_molar(m.type, m.value, m.unit)
            assert lig.ddg_kcal_mol == pytest.approx(RT * math.log(x / xr), abs=1e-9)
            if m.error is None or ref.measurement.error is None:
                assert lig.ddg_sigma_kcal_mol is None
            elif lig.name != t.reference:
                f = math.log(10) if m.type == "pic50" else 1.0
                s = f * m.error / (1 if m.type == "pic50" else m.value)
                sr = f * ref.measurement.error / (1 if m.type == "pic50" else ref.measurement.value)
                assert lig.ddg_sigma_kcal_mol == pytest.approx(RT * math.hypot(s, sr), abs=1e-9)


def test_known_values():
    assert bm.ddg_kcal_mol(1e-5, 1e-6) == pytest.approx(RT * math.log(10))
    assert bm.ddg_kcal_mol(1e-6, 1e-5) == pytest.approx(-1.3642, abs=1e-3)  # one log unit is 1.364 kcal/mol
    assert bm.to_molar("pic50", 9.0, "") == pytest.approx(1e-9)
    assert bm.to_molar("ki", 5, "nM") == pytest.approx(5e-9)
    assert bm.sigma_ln("ic50", 2.0, "uM", 0.5) == pytest.approx(0.25)
    assert bm.sigma_ln("pic50", 8.0, "", 0.1) == pytest.approx(0.1 * math.log(10))
    assert bm.sigma_ln("ki", 2.0, "uM", None) is None


def test_ddg_between_is_antisymmetric(bench):
    t = bench.targets["cdk2"]
    a, b = t.ligands[0].name, t.ligands[1].name
    assert bm.ddg_between(t, a, b) == pytest.approx(-bm.ddg_between(t, b, a))


def test_ki_and_ic50_targets_are_labelled(bench):
    assert {n for n, t in bench.targets.items() if t.assay_type == "ki"} == {"mcl1", "ptp1b", "thrombin", "tyk2"}
    assert bench.targets["pde2"].assay_type == "pic50"


def test_noise_floor_matches_the_constant(bench):
    pocket = pytest.importorskip("smeltery.pocket.binding_energy")
    assert bench.noise_floor_kcal_mol == pocket.DDE_NOISE_FLOOR_KCAL_MOL


def test_suitability_recomputes_from_smiles(bench, monkeypatch):
    """The stored per-target statistics are not asserted: charge, size and the single-site count recompute.

    An MCS search that finishes gives the same answer whatever its time limit, but one cut off by the 2 s
    wall-clock limit is counted as NOT single-site, so on a loaded machine the count changes for reasons that
    are not chemistry. This test therefore allows a generous limit and requires that no search was cut off.
    """
    pytest.importorskip("rdkit")
    monkeypatch.setattr(suit, "MCS_TIMEOUT_S", 120)
    for name in ("cdk2", "pde2", "cmet"):
        t = bench.targets[name]
        raw_t = bench.raw["targets"][name]
        for lig, rl in zip(t.ligands, raw_t["ligands"]):
            chem = bm.ligand_chemistry(lig.smiles_upstream)
            assert chem == rl["chemistry"]
            assert bm.heavy_canonical(lig.smiles_upstream) == lig.smiles
        ref = t.ligand(t.reference)
        results = [
            suit.single_site_change(ref.smiles_upstream, lg.smiles_upstream)
            for lg in t.ligands
            if lg.name != t.reference
        ]
        assert not any(r["timed_out"] for r in results), f"{name}: an MCS search was cut off; the count is not valid"
        assert sum(r["single_site"] for r in results) == t.suitability["n_single_site_vs_reference"]


def test_a_cut_off_mcs_search_is_never_counted_as_a_single_site_change(monkeypatch):
    """The semantics the test above depends on: a timed-out search reports `timed_out` and is not single-site."""
    pytest.importorskip("rdkit")
    from rdkit.Chem import rdFMCS

    real = rdFMCS.FindMCS

    class CutOff:
        def __init__(self, res):
            self.smartsString, self.numAtoms, self.canceled = res.smartsString, res.numAtoms, True

    monkeypatch.setattr(rdFMCS, "FindMCS", lambda *a, **k: CutOff(real(*a, **k)))
    r = suit.single_site_change("c1ccccc1N", "c1ccccc1Cl")  # a textbook single-site change when the search completes
    assert r["timed_out"] is True and r["single_site"] is False


def test_power_recomputes_from_ddg(bench):
    for t in bench.targets.values():
        ddg = [lg.ddg_kcal_mol for lg in t.ligands]
        idx = [lg.name for lg in t.ligands].index(t.reference)
        got, stored = bm.power_summary(ddg, bench.noise_floor_kcal_mol, idx), t.suitability["power"]
        assert got.keys() == stored.keys()
        for key, value in got.items():
            # Integer counts must match exactly; a derived float may differ in its last digit between platforms
            # (numpy/Python build), which is not a difference in the statistic.
            assert (
                value == pytest.approx(stored[key], rel=1e-12, abs=0)
                if isinstance(value, float)
                else value == stored[key]
            ), key


def test_reference_is_a_most_connected_ligand(bench):
    pytest.importorskip("rdkit")
    t = bench.targets["cdk2"]
    assert t.reference == "lig_1h1q"  # the unsubstituted parent: 9 of 9 analogues are single-site changes
    assert t.suitability["n_single_site_vs_reference"] == 9


def test_assay_variability_is_stated_honestly(bench):
    v = bench.assay_variability
    assert "within_benchmark" in v and "literature" in v
    assert v["literature"]["doi"] == "10.1021/jm300131x"
    assert "full text" in v["literature"]["scope"]


# ---------------------------------------------------------------- the loader refuses


def _mutate(raw: dict, path: tuple, value) -> dict:
    d = copy.deepcopy(raw)
    cur = d
    for k in path[:-1]:
        cur = cur[k]
    if value is KeyError:
        del cur[path[-1]]
    else:
        cur[path[-1]] = value
    return d


T0 = ("targets", "cdk2")
CASES = {
    "licence.attribution removed": (("licence", "attribution"), KeyError),
    "licence.attribution blank": (("licence", "attribution"), "  "),
    "licence.attribution without licence name": (("licence", "attribution"), "Open Forcefield Group"),
    "licence.data_licence empty": (("licence", "data_licence"), ""),
    "licence.data_licence blank": (("licence", "data_licence"), "   "),
    "licence removed": (("licence",), KeyError),
    "commit removed": (("provenance", "upstream_commit"), KeyError),
    "commit malformed": (("provenance", "upstream_commit"), "main"),
    "retrieval_date removed": (("provenance", "retrieval_date"), KeyError),
    "file sha removed": (T0 + ("files", 0, "sha256"), KeyError),
    "file sha malformed": (T0 + ("files", 0, "sha256"), "abc"),
    "file url empty": (T0 + ("files", 0, "url"), ""),
    "unit unknown": (T0 + ("ligands", 0, "measurement", "unit"), "mg/mL"),
    "unit removed": (T0 + ("ligands", 0, "measurement", "unit"), KeyError),
    "pic50 with a unit": (("targets", "pde2", "ligands", 0, "measurement", "unit"), "nM"),
    "value zero": (T0 + ("ligands", 0, "measurement", "value"), 0),
    "value negative": (T0 + ("ligands", 0, "measurement", "value"), -1.0),
    "type unknown": (T0 + ("ligands", 0, "measurement", "type"), "kd"),
    "sources removed": (T0 + ("ligands", 0, "measurement", "sources"), KeyError),
    "sources empty": (T0 + ("ligands", 0, "measurement", "sources"), []),
    "source not a DOI": (T0 + ("ligands", 0, "measurement", "sources", 0, "id"), "jm0311442"),
    "error key removed": (T0 + ("ligands", 0, "measurement", "error"), KeyError),
    "error negative": (T0 + ("ligands", 0, "measurement", "error"), -1),
    "ddG tampered": (T0 + ("ligands", 1, "ddg_kcal_mol"), 123.0),
    "ddG sigma tampered": (T0 + ("ligands", 1, "ddg_sigma_kcal_mol"), 123.0),
    "reference unknown": (T0 + ("reference",), "lig_nope"),
    "protonation empty": (T0 + ("protonation",), ""),
    "assay type mismatch": (T0 + ("assay_type",), "ki"),
    "temperature non-positive": (("temperature_k",), 0),
    "suitability removed": (T0 + ("suitability",), KeyError),
    "assay_variability removed": (("assay_variability",), KeyError),
}


def test_unmutated_manifest_parses(raw):
    bm.parse_manifest(copy.deepcopy(raw))


@pytest.mark.parametrize("label", sorted(CASES))
def test_loader_refuses(raw, label):
    path, value = CASES[label]
    with pytest.raises(bm.BenchmarkError):
        bm.parse_manifest(_mutate(raw, path, value))


def test_load_refuses_unreadable_manifest(tmp_path):
    p = tmp_path / "m.json"
    p.write_text("{not json")
    with pytest.raises(bm.BenchmarkError):
        bm.load_benchmark(p)
    with pytest.raises(bm.BenchmarkError):
        bm.load_benchmark(tmp_path / "absent.json")


@pytest.mark.parametrize(
    "args",
    [
        ("ic50", 0.0, "uM"),
        ("ic50", -3.0, "uM"),
        ("ic50", float("nan"), "uM"),
        ("ki", 1.0, "furlong"),
        ("dg", 1.0, "nM"),
    ],
)
def test_to_molar_refuses(args):
    with pytest.raises(bm.BenchmarkError):
        bm.to_molar(*args)


# ---------------------------------------------------------------- fetching (fake downloader; live is opt-in)


def _fake_bench(raw: dict, payloads: dict[str, bytes]) -> bm.Benchmark:
    d = copy.deepcopy(raw)
    t = d["targets"]["cdk2"]
    for f in t["files"]:
        data = payloads.setdefault(f["url"], f"fake {f['kind']}\n".encode())
        f["sha256"] = hashlib.sha256(data).hexdigest()
    return bm.parse_manifest(d)


def test_fetch_with_fake_downloader_verifies_and_writes(raw, tmp_path):
    payloads: dict[str, bytes] = {}
    b = _fake_bench(raw, payloads)
    paths = bm.fetch_files(b, tmp_path, targets=["cdk2"], downloader=lambda u: payloads[u])
    assert sorted(p.name for p in paths) == ["ligands.sdf", "protein.pdb"]
    assert (tmp_path / "cdk2" / "protein.pdb").read_bytes() == b"fake protein_pdb\n"


def test_fetch_refuses_digest_mismatch_and_leaves_nothing(raw, tmp_path):
    payloads: dict[str, bytes] = {}
    b = _fake_bench(raw, payloads)
    bad = {u: d + b"tampered" for u, d in payloads.items()}
    with pytest.raises(bm.BenchmarkError, match="sha256"):
        bm.fetch_files(b, tmp_path, targets=["cdk2"], downloader=lambda u: bad[u])
    assert not list(tmp_path.rglob("*.*"))


def test_fetch_refuses_unknown_target(bench, tmp_path):
    with pytest.raises(bm.BenchmarkError, match="unknown targets"):
        bm.fetch_files(bench, tmp_path, targets=["nope"], downloader=lambda u: b"")


def _script():
    spec = importlib.util.spec_from_file_location("fetch_plb", ROOT / "scripts" / "fetch_plb.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_fetch_script_refuses_without_opt_in(tmp_path, monkeypatch):
    monkeypatch.delenv("SMELTERY_NETWORK", raising=False)
    assert _script().main([str(tmp_path)]) == 2


@pytest.mark.skipif(os.environ.get("SMELTERY_NETWORK") != "1", reason="live download is opt-in: SMELTERY_NETWORK=1")
def test_live_download_matches_pinned_digests(bench, tmp_path):
    paths = bm.fetch_files(bench, tmp_path, targets=["cdk2"], kinds=("protein_pdb", "ligands_sdf", "ligands_yml"))
    assert len(paths) == 3
