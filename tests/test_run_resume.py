"""A plain `smeltery run --out OUT` checkpoints every SCF to OUT/calcs.partial.jsonl as it finishes; a rerun into the
same OUT reuses those lines, so a crash hours in costs only the SCFs that had not finished."""

import json

import pytest
from golden_path import results_without_timing
from test_cli import SMALL

from smeltery.cli import main
from smeltery.tiers import FieldInteraction, refuse_false_convergence


class Interrupted(RuntimeError):
    pass


def _count_scfs(monkeypatch, fail_after: int | None = None) -> list[int]:
    """Count real SCFs; with `fail_after`, the next SCF after that many raises (a crash mid-run)."""
    calls = [0]
    real = FieldInteraction.scf

    def scf(self, ferric, mol, bs, charges, rung):
        if fail_after is not None and calls[0] >= fail_after:
            raise Interrupted("killed")
        calls[0] += 1
        return real(self, ferric, mol, bs, charges, rung)

    monkeypatch.setattr(FieldInteraction, "scf", scf)
    return calls


def _record(out):
    (path,) = out.glob("run-*.json")
    return json.loads(path.read_text())


def _without_seconds(path):
    return [{k: v for k, v in json.loads(x).items() if k != "seconds"} for x in path.read_text().splitlines()]


@pytest.mark.needs_ferric
def test_an_interrupted_run_resumes_from_its_checkpoint_with_the_same_record(tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "c.toml"
    cfg.write_text(SMALL.format(floor=0.0))
    n_total, k = 18, 7  # 3 candidates x 3 poses x (vacuum + field)

    with monkeypatch.context() as m:
        fresh = _count_scfs(m)
        assert main(["run", str(cfg), "--out", str(tmp_path / "fresh")]) == 0
    assert fresh[0] == n_total
    assert not (tmp_path / "fresh" / "calcs.partial.jsonl").exists()  # a finished run leaves today's outputs only
    assert not (tmp_path / "fresh" / "progress.json").exists()

    out = tmp_path / "resumed"
    with monkeypatch.context() as m:
        _count_scfs(m, fail_after=k)
        assert main(["run", str(cfg), "--out", str(out)]) == 3
    assert not list(out.glob("run-*.json"))
    assert len((out / "calcs.partial.jsonl").read_text().splitlines()) == k  # each SCF flushed as it finished
    progress = json.loads((out / "progress.json").read_text())
    assert progress["total_scfs"] == n_total and progress["partial"] == "calcs.partial.jsonl"
    capsys.readouterr()

    with monkeypatch.context() as m:
        again = _count_scfs(m)
        assert main(["run", str(cfg), "--out", str(out)]) == 0  # no record yet: resumes without --force
    assert again[0] == n_total - k
    assert f"resuming: {k} of {n_total} SCFs reused" in capsys.readouterr().out

    a, b = _record(tmp_path / "fresh"), _record(out)
    assert a["input_digest"] == b["input_digest"]
    assert results_without_timing(a["results"]) == results_without_timing(b["results"])
    assert _without_seconds(tmp_path / "fresh" / "calcs.jsonl") == _without_seconds(out / "calcs.jsonl")
    assert not (out / "calcs.partial.jsonl").exists() and not (out / "progress.json").exists()

    assert main(["run", str(cfg), "--out", str(out)]) == 2  # a finished run still needs --force


@pytest.mark.needs_ferric
def test_a_checkpoint_of_other_inputs_is_refused_and_force_discards_it(tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "c.toml"
    cfg.write_text(SMALL.format(floor=0.0))
    out = tmp_path / "o"
    with monkeypatch.context() as m:
        _count_scfs(m, fail_after=3)
        assert main(["run", str(cfg), "--out", str(out)]) == 3
    cfg.write_text(SMALL.format(floor=0.0).replace("n_poses = 3", "n_poses = 2"))  # other inputs, other digest
    capsys.readouterr()
    assert main(["run", str(cfg), "--out", str(out)]) == 2
    err = capsys.readouterr().err
    assert "calcs.partial.jsonl" in err and "--force" in err
    with monkeypatch.context() as m:
        calls = _count_scfs(m)
        assert main(["run", str(cfg), "--out", str(out), "--force"]) == 0
    assert calls[0] == 12


def test_a_replayed_rung_keeps_the_refusal_the_first_run_recorded(tmp_path):
    """A rung whose convergence the ladder refused (`refuse_false_convergence`) is stored refused, so a resumed leg
    climbs past it exactly as the first run did -- no ferric: the checkpoint is driven by hand."""
    from smeltery.checkpoint import ScfCheckpoint

    cp = ScfCheckpoint(tmp_path, "d" * 64, "c" * 64, "amber")
    assert cp.n_reusable == 0
    low = {"candidate": "x", "pose": 0, "leg": "field", "converged": False, "iterations": 9}
    cp.put({**low, "energy_hartree": -100.0, "seconds": 1.0, "rung": 0})
    # rung 1 "converged" 0.8 Ha above rung 0: the ladder refuses it, and the refusal is what is stored
    refused = refuse_false_convergence(
        {**low, "converged": True, "energy_hartree": -99.2, "seconds": 2.0, "rung": 1},
        [{"energy_hartree": -100.0}],
    )
    assert refused["converged"] is False and refused["engine_converged"] is True
    cp.put(refused)

    again = ScfCheckpoint(tmp_path, "d" * 64, "c" * 64, "amber")
    assert again.n_reusable == 2
    back = again.get("x", 0, "field", 1)
    assert back["converged"] is False and back["engine_converged"] is True and "refused" in back
    assert "digest" not in back and "config_sha256" not in back  # the stamps are not part of the tier's line
    assert again.get("x", 0, "field", 2) is None and again.get("y", 0, "field", 0) is None
    assert again.get("x", 0, "vacuum", 0) is None  # another leg of the same pose is its own identity
