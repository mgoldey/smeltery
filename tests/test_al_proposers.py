"""`smeltery.al_proposers.UncertaintyProposer`: the Proposer protocol, determinism, cold start, dedup, the no-RDKit
fallback, the CLI wiring, and a synthetic-landscape comparison against `PoolProposer` (a cheap fake scorer; no
funnel, no quantum chemistry)."""

from __future__ import annotations

import al_synthetic as syn
import numpy as np
import pytest

from smeltery import al, al_proposers
from smeltery.al import PoolProposer, ScoredCandidate
from smeltery.al_proposers import UncertaintyProposer


def _hist(pairs, it=0, sem=0.05):
    return [ScoredCandidate(al._canonical(s), f"n{i}", v, sem, 4, it) for i, (s, v) in enumerate(pairs)]


POOL = syn.POOL


def test_honours_the_protocol_and_exposes_seeds_and_params():
    p = UncertaintyProposer(POOL, seed=3, acquisition="ei")
    assert isinstance(p, al.Proposer)
    assert p.seeds == {"uncertainty": 3}
    ident = al.proposer_identity(p, "smeltery.al_proposers:UncertaintyProposer")
    assert ident["class"] == "UncertaintyProposer" and ident["params"]["acquisition"] == "ei"
    assert al._proposer_seeds(p) == {"uncertainty": 3}
    # a proposer without `params` keeps its old identity
    assert "params" not in al.proposer_identity(PoolProposer(POOL), None)
    with pytest.raises(ValueError):
        UncertaintyProposer(POOL, acquisition="nope")


def test_cold_start_is_a_seeded_draw_and_matches_the_pool_proposer():
    a = UncertaintyProposer(POOL, seed=5).propose([], 4)
    assert a == UncertaintyProposer(POOL, seed=5).propose([], 4)
    assert a != UncertaintyProposer(POOL, seed=6).propose([], 4)
    assert a == PoolProposer(POOL, seed=5).propose([], 4)
    one = _hist([(POOL[0], -1.0)])  # a single score is still too little to fit
    assert len(UncertaintyProposer(POOL, seed=5).propose(one, 4)) == 4


def test_deterministic_given_seed_and_history():
    h = _hist([(POOL[i], syn.score(POOL[i])) for i in range(0, 40, 4)])
    a = UncertaintyProposer(POOL, seed=1).propose(h, 5)
    assert a == UncertaintyProposer(POOL, seed=1).propose(h, 5)
    assert a == UncertaintyProposer(POOL, seed=1).propose(h, 5)  # repeat calls do not drift


@pytest.mark.parametrize("acq", ["lcb", "ei", "random"])
def test_never_reproposes_scored_or_excluded_molecules(acq):
    h = _hist([(POOL[i], syn.score(POOL[i])) for i in range(30)])
    ref = POOL[40]
    p = UncertaintyProposer(POOL + [POOL[3], "not a smiles"], acquisition=acq, exclude=[ref])
    out = p.propose(h, 12)
    assert len(out) == 12 == len(set(out))
    assert not set(out) & ({c.smiles for c in h} | {ref})


def test_returns_fewer_when_the_pool_runs_out_and_handles_n_zero():
    h = _hist([(POOL[i], syn.score(POOL[i])) for i in range(len(POOL) - 3)])
    p = UncertaintyProposer(POOL)
    assert len(p.propose(h, 10)) == 3
    assert p.propose(h, 0) == []
    assert UncertaintyProposer([]).propose([], 3) == []


def test_unscored_history_rows_are_excluded_but_not_fitted():
    h = [ScoredCandidate(POOL[0], "ref", None, None, None, 0), ScoredCandidate(POOL[1], "x", float("nan"), None, 1, 0)]
    out = UncertaintyProposer(POOL, seed=0).propose(h, 5)
    assert len(out) == 5 and POOL[0] not in out and POOL[1] not in out


def test_missing_sem_does_not_break_the_fit():
    h = [ScoredCandidate(POOL[i], f"n{i}", syn.score(POOL[i]), None, 4, 0) for i in range(6)]
    assert len(UncertaintyProposer(POOL).propose(h, 3)) == 3


def test_a_generator_extends_the_pool_and_is_deterministic():
    def gen(rng, k):
        return [f"{'C' * rng.randint(2, 9)}O" for _ in range(k)] + ["junk("]

    h = _hist([("CCO", 0.0), ("CCCO", -0.2), ("CCCCO", -0.1)])
    a = UncertaintyProposer((), generator=gen, seed=2).propose(h, 4)
    assert a == UncertaintyProposer((), generator=gen, seed=2).propose(h, 4)
    assert len(a) == 4 and not set(a) & {"CCO", "CCCO", "CCCCO"}


def test_the_acquisition_prefers_a_good_region_and_ei_and_lcb_agree_on_it():
    # train on one scaffold's fluorinated and acidic members; the model should rank fluorinated unscored ahead
    good = [s for s in POOL if "F" in s][:10]
    bad = [s for s in POOL if "C(=O)O" in s][:10]
    h = _hist([(s, syn.score(s)) for s in good + bad])
    for acq in ("lcb", "ei"):
        picks = UncertaintyProposer(POOL, acquisition=acq).propose(h, 6)
        mean_true = np.mean([syn.score(s) for s in picks])
        assert mean_true < np.mean([syn.score(s) for s in POOL]), acq


def test_without_rdkit_it_still_fits_proposes_and_dedups(monkeypatch):
    monkeypatch.setattr(al_proposers, "_rdkit", lambda: None)
    assert al_proposers.canonical(" CCO ") == "CCO" and al_proposers.canonical("") is None
    p = UncertaintyProposer(["CCO", "CCCO", "CCCCO", "CCCCCO", "CCCCCCO", "CCO"], seed=1)
    assert p.params["featurizer"] == "smiles-3gram-hash"
    h = [ScoredCandidate("CCO", "a", 0.0, 0.1, 4, 0), ScoredCandidate("CCCO", "b", -0.5, 0.1, 4, 0)]
    out = p.propose(h, 2)
    assert len(out) == 2 and not set(out) & {"CCO", "CCCO"}
    assert out == UncertaintyProposer(["CCO", "CCCO", "CCCCO", "CCCCCO", "CCCCCCO"], seed=1).propose(h, 2)
    x = al_proposers.featurize(["CCCO", "CCCO"], use_rdkit=False)
    assert (x[0] == x[1]).all() and x.sum() > 0


def test_tanimoto_kernel_is_symmetric_psd_with_unit_diagonal():
    x = al_proposers.featurize(POOL[:30])
    k = al_proposers.tanimoto_kernel(x, x)
    assert np.allclose(k, k.T) and np.allclose(np.diag(k), 1.0)
    assert np.linalg.eigvalsh(k).min() > -1e-8


def test_cli_wires_acquisition_to_the_proposer(tmp_path, monkeypatch):
    pool = tmp_path / "pool.smi"
    pool.write_text("\n".join(POOL[:20]))
    seen = {}

    def fake_run_loop(**kw):
        seen.update(kw)
        return al.LoopManifest(loop="x", reference={}, proposer={}, target="t", budget_cpu_h=None)

    monkeypatch.setattr(al, "run_loop", fake_run_loop)
    argv = [
        "run",
        "--loop",
        "x",
        "--target",
        "t",
        "--reference-smiles",
        "CCO",
        "--pool",
        str(pool),
        "--iterations",
        "1",
        "--batch",
        "2",
        "--out",
        str(tmp_path / "o"),
    ]
    assert al.al_main(argv + ["--acquisition", "ei", "--seed", "9"], env={}) == 0
    p = seen["proposer"]
    assert isinstance(p, UncertaintyProposer) and p.acquisition == "ei" and p.seeds == {"uncertainty": 9}
    assert seen["proposer_ref"] == "smeltery.al_proposers:UncertaintyProposer"
    assert al.al_main(argv, env={}) == 0 and isinstance(seen["proposer"], PoolProposer)  # default unchanged
    no_pool = ["run", "--loop", "x", "--target", "t", "--reference-smiles", "CCO", "--iterations", "1", "--batch", "2"]
    assert al.al_main([*no_pool, "--out", str(tmp_path / "o"), "--acquisition", "lcb"], env={}) == 2  # needs --pool


def test_load_proposer_ref_needs_a_pool_so_the_flag_is_the_cli_route():
    # `--proposer module:Class` instantiates the class; a zero-arg UncertaintyProposer has an empty pool and
    # exhausts cleanly (the loop then stops `exhausted`) rather than crashing
    p = al.load_proposer("smeltery.al_proposers:UncertaintyProposer")
    assert isinstance(p, UncertaintyProposer)
    assert p.propose([], 4) == []


def test_synthetic_landscape_beats_the_random_draw(capsys):
    """Best-score-so-far vs iteration on a 312-molecule SAR landscape, 20 seeds, batch 4, 6 iterations. The cold start
    is the same seeded draw, so iteration 0 is identical; after that the model-guided curves must be lower on average
    at every iteration, and at the end by a clear margin. The measured numbers are printed (`pytest -s`)."""
    res = syn.compare(range(20), iterations=6, batch=4)
    mean = {k: np.mean(v, axis=0) for k, v in res.items()}
    with capsys.disabled():
        print(f"\n[synthetic] pool size {len(POOL)}, global optimum {syn.BEST:.3f}")
        for k, v in mean.items():
            print(f"[synthetic] {k:>4}: mean best-so-far by iteration " + " ".join(f"{x:.3f}" for x in v))
    for acq in ("lcb", "ei"):
        assert mean[acq][0] == pytest.approx(mean["pool"][0])
        assert (mean[acq][1:] < mean["pool"][1:]).all(), acq
        assert mean[acq][-1] < mean["pool"][-1] - 0.3, acq
