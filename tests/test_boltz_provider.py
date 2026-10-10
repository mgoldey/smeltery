"""BoltzProvider (#23): runner-injected, so none of this needs boltz or its weights.

The fake runner writes the files `boltz predict` writes (layout from boltz docs/prediction.md).
`SYNTH_*` helpers build SYNTHETIC outputs to exercise failure modes; the committed REAL output of
a live run (tests/data/boltz_trpcage/, see its PROVENANCE.md) is parsed in the fixture tests.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from smeltery import RunRecord
from smeltery.providers import (
    BOLTZ_ATTRIBUTION,
    BoltzProvider,
    RunOutcome,
    StructureProvider,
    StructureResult,
)

DATA = Path(__file__).parent / "data" / "boltz_trpcage"
LAYOUT = "boltz_results_query"  # as measured in the live run (docs omit it: test_documented_layout_also_accepted)
SEQ = "NLYIQWLKDGGPSSGRPPPS"  # Trp-cage TC5b, 20 residues


def _synth_pdb(n: int, b: float = 87.5) -> str:
    rows = []
    for i in range(1, n + 1):
        rows.append(f"ATOM  {i:5d}  CA  GLY A{i:4d}    {i * 3.8:8.3f}   0.000   0.000  1.00{b:6.2f}           C")
    return "\n".join(rows) + "\nEND\n"


GOOD_CONF = {"confidence_score": 0.84, "ptm": 0.5, "iptm": 0.0, "complex_plddt": 0.875}


def make_runner(pdb=None, conf=GOOD_CONF, returncode=0, stderr="", write=True, calls=None):
    """A fake `boltz predict`: writes predictions/query/{query_model_0.pdb, confidence_query_model_0.json}."""

    def runner(argv, timeout):
        if calls is not None:
            calls.append((argv, timeout))
        out = Path(argv[argv.index("--out_dir") + 1]) / LAYOUT / "predictions" / "query"
        if write and returncode == 0:
            out.mkdir(parents=True)
            if pdb is not None:
                (out / "query_model_0.pdb").write_text(pdb)
            if conf is not None:
                (out / "confidence_query_model_0.json").write_text(conf if isinstance(conf, str) else json.dumps(conf))
        return RunOutcome(returncode, "", stderr)

    return runner


def test_success_carries_plddt_licence_attribution_and_weights():
    p = BoltzProvider(make_runner(pdb=_synth_pdb(20)), boltz_version="2.2.1")
    res = p.predict(SEQ)
    assert res is not None and p.last_error is None
    assert res.kind == "PREDICTED" and res.license_id == "MIT"
    assert res.attribution == BOLTZ_ATTRIBUTION and "10.1101/2025.06.14.659707" in res.attribution
    assert "boltz-community/boltz-2" in res.weights_source
    assert len(res.plddt) == 20 and res.mean_plddt == pytest.approx(87.5)
    assert res.metadata["boltz_confidence"]["complex_plddt"] == 0.875
    assert res.metadata["boltz_version"] == "2.2.1" and res.metadata["plddt_scale"].startswith("0-100")
    d = res.to_dict()
    json.dumps(d)
    assert d["license_id"] == "MIT" and d["attribution"] and d["mean_plddt"] == pytest.approx(87.5)


def test_conforms_to_the_structure_provider_protocol():
    p = BoltzProvider(make_runner(pdb=_synth_pdb(20)))
    assert isinstance(p, StructureProvider) and p.name == "boltz2"


def test_command_line_and_yaml_sent_to_the_runner(tmp_path):
    calls, yamls = [], []

    def runner(argv, timeout):
        yamls.append(Path(argv[2]).read_text())
        return make_runner(pdb=_synth_pdb(20), calls=calls)(argv, timeout)

    BoltzProvider(runner, accelerator="cpu", seed=7, cache_dir=str(tmp_path), timeout_s=99).predict(SEQ.lower())
    argv, timeout = calls[0]
    assert argv[:2] == ["boltz", "predict"] and timeout == 99
    assert argv[argv.index("--accelerator") + 1] == "cpu" and argv[argv.index("--output_format") + 1] == "pdb"
    assert argv[argv.index("--seed") + 1] == "7" and argv[argv.index("--cache") + 1] == str(tmp_path)
    assert "--use_msa_server" not in argv  # no sequence leaves the machine by default
    assert f"sequence: {SEQ}" in yamls[0] and "msa: empty" in yamls[0] and "ligand" not in yamls[0]


def test_ligand_and_msa_server_options():
    calls, yamls = [], []

    def runner(argv, timeout):
        yamls.append(Path(argv[2]).read_text())
        return make_runner(pdb=_synth_pdb(20), calls=calls)(argv, timeout)

    res = BoltzProvider(runner, use_msa_server=True).predict(SEQ, ligand_smiles="CCO")
    assert "--use_msa_server" in calls[0][0] and "msa: empty" not in yamls[0]
    assert "smiles: 'CCO'" in yamls[0] and res.metadata["ligand_smiles"] == "CCO"
    assert "ColabFold" in res.attribution  # the server's citation is carried when it was used


@pytest.mark.parametrize("spec", ["", "NLY IQ", "NLYBZ", "12345", "NLYIQ*"])
def test_bad_sequence_is_refused_without_running(spec):
    calls = []
    p = BoltzProvider(make_runner(pdb=_synth_pdb(5), calls=calls))
    assert p.predict(spec) is None and "amino acids" in p.last_error and calls == []


def test_bad_smiles_is_refused_without_running():
    calls = []
    p = BoltzProvider(make_runner(pdb=_synth_pdb(20), calls=calls))
    assert p.predict(SEQ, ligand_smiles="C C'") is None and "SMILES" in p.last_error and calls == []


def test_missing_install_is_actionable():
    def runner(argv, timeout):
        raise FileNotFoundError("boltz")

    p = BoltzProvider(runner)
    assert p.predict(SEQ) is None
    assert "separate environment" in p.last_error and "docs/boltz.md" in p.last_error


def test_default_runner_reports_missing_executable():
    p = BoltzProvider(executable="definitely-not-a-boltz-binary")
    assert p.predict(SEQ) is None and "separate environment" in p.last_error and "docs/boltz.md" in p.last_error


def test_nonzero_exit_reports_code_and_stderr_tail():
    p = BoltzProvider(make_runner(returncode=3, stderr="Traceback ... RuntimeError: boom"))
    assert p.predict(SEQ) is None and "exited 3" in p.last_error and "boom" in p.last_error


def test_gpu_absence_suggests_cpu():
    p = BoltzProvider(make_runner(returncode=1, stderr="RuntimeError: No CUDA GPUs are available"), accelerator="gpu")
    assert p.predict(SEQ) is None and "accelerator='cpu'" in p.last_error
    q = BoltzProvider(make_runner(returncode=1, stderr="RuntimeError: No CUDA GPUs are available"), accelerator="cpu")
    assert q.predict(SEQ) is None and "retry" not in q.last_error  # the hint is for gpu runs only


def test_timeout_is_a_reason_not_a_crash():
    def runner(argv, timeout):
        raise subprocess.TimeoutExpired(argv, timeout)

    p = BoltzProvider(runner, timeout_s=5)
    assert p.predict(SEQ) is None and "timed out after 5" in p.last_error


def test_exit_zero_but_missing_outputs_is_not_a_structure():
    assert (p := BoltzProvider(make_runner(write=False))).predict(SEQ) is None and "did not write" in p.last_error
    assert (p := BoltzProvider(make_runner(pdb=None))).predict(SEQ) is None and "query_model_0.pdb" in p.last_error
    p = BoltzProvider(make_runner(pdb=_synth_pdb(20), conf=None))
    assert p.predict(SEQ) is None and "confidence_query_model_0.json" in p.last_error


@pytest.mark.parametrize(
    "conf, why",
    [
        ("{not json", "unusable"),
        ("[1, 2]", "not an object"),
        ({"confidence_score": 0.8}, "complex_plddt"),
        ({"confidence_score": 0.8, "complex_plddt": 87.5}, "[0, 1]"),  # 0-100 where 0-1 is documented
        ({"confidence_score": 0.8, "complex_plddt": float("nan")}, "[0, 1]"),
        ({"confidence_score": 0.8, "complex_plddt": "0.9"}, "[0, 1]"),
        ({"confidence_score": 0.8, "complex_plddt": True}, "[0, 1]"),
    ],
)
def test_malformed_confidence_is_rejected(conf, why):
    p = BoltzProvider(make_runner(pdb=_synth_pdb(20), conf=conf if isinstance(conf, dict) else conf))
    assert p.predict(SEQ) is None and why in p.last_error


def test_model_that_disagrees_with_the_input_is_rejected():
    p = BoltzProvider(make_runner(pdb=_synth_pdb(19)))
    assert p.predict(SEQ) is None and "19 residues" in p.last_error and "20" in p.last_error
    q = BoltzProvider(make_runner(pdb="REMARK nothing\nEND\n"))
    assert q.predict(SEQ) is None and "0 residues" in q.last_error


def test_out_of_range_bfactors_are_rejected():
    p = BoltzProvider(make_runner(pdb=_synth_pdb(20, b=150.0)))
    assert p.predict(SEQ) is None and "outside 0-100" in p.last_error


def test_last_error_resets_and_nothing_is_fabricated_on_failure():
    fail = BoltzProvider(make_runner(returncode=1, stderr="x"))
    assert fail.predict(SEQ) is None and fail.last_error
    ok = BoltzProvider(make_runner(pdb=_synth_pdb(20)))
    assert ok.predict("bad!") is None and ok.last_error
    assert ok.predict(SEQ) is not None and ok.last_error is None


def test_result_invariants_still_hold():
    with pytest.raises(ValueError, match="licence"):
        StructureResult(text="ATOM", kind="PREDICTED", source="x", plddt=((1, 90.0),))
    with pytest.raises(ValueError, match="cannot be labelled EXPERIMENTAL"):
        StructureResult(text="ATOM", kind="EXPERIMENTAL", source="x", weights_source="boltz-2")


def test_settings_roundtrip_through_a_run_record_with_stable_digest():
    s = BoltzProvider(make_runner(), seed=1, sampling_steps=50).settings()
    assert s["license_id"] == "MIT" and s["model"] == "boltz-2" and s["sampling_steps"] == 50
    rec = RunRecord("boltz-demo", {"structure_provider": s, "sequence": SEQ}, [], {})
    back = RunRecord.from_json(rec.to_json())
    assert back.inputs["structure_provider"] == s and back.input_digest == rec.input_digest
    same = RunRecord(
        "boltz-demo",
        {"structure_provider": BoltzProvider(seed=1, sampling_steps=50).settings(), "sequence": SEQ},
        [],
        {},
    )
    other = RunRecord(
        "boltz-demo",
        {"structure_provider": BoltzProvider(seed=2, sampling_steps=50).settings(), "sequence": SEQ},
        [],
        {},
    )
    assert same.input_digest == rec.input_digest != other.input_digest


def test_settings_do_not_depend_on_the_runner():
    assert BoltzProvider(make_runner()).settings() == BoltzProvider().settings()


# --- committed REAL output of a live run (see tests/data/boltz_trpcage/PROVENANCE.md) ---------------

needs_fixture = pytest.mark.skipif(not (DATA / "query_model_0.pdb").exists(), reason="no committed live fixture")


def _replay_runner(argv, timeout):
    out = Path(argv[argv.index("--out_dir") + 1]) / LAYOUT / "predictions" / "query"
    out.mkdir(parents=True)
    shutil.copy(DATA / "query_model_0.pdb", out / "query_model_0.pdb")
    shutil.copy(DATA / "confidence_query_model_0.json", out / "confidence_query_model_0.json")
    return RunOutcome(0)


@needs_fixture
def test_parser_on_real_boltz2_output():
    p = BoltzProvider(_replay_runner, boltz_version="2.2.1")
    res = p.predict(SEQ)
    assert res is not None, p.last_error
    assert res.kind == "PREDICTED" and len(res.plddt) == 20
    conf = json.loads((DATA / "confidence_query_model_0.json").read_text())
    assert res.metadata["boltz_confidence"]["complex_plddt"] == pytest.approx(conf["complex_plddt"], abs=1e-4)
    # PDB B-factors are pLDDT x 100 and complex_plddt is 0-1: the two agree to a few points
    assert res.mean_plddt == pytest.approx(100 * conf["complex_plddt"], abs=3.0)


# --- opt-in live run ------------------------------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("SMELTERY_RUN_BOLTZ") != "1" or shutil.which(os.environ.get("SMELTERY_BOLTZ_EXE", "boltz")) is None,
    reason="live Boltz-2 run: needs boltz installed (separate env; docs/boltz.md), "
    "~6 GB of weights in ~/.boltz (or --cache) "
    "and minutes of CPU/GPU; set SMELTERY_RUN_BOLTZ=1 (and SMELTERY_BOLTZ_EXE=/path/to/boltz if not on PATH)",
)
def test_live_boltz2_trp_cage():
    p = BoltzProvider(
        executable=os.environ.get("SMELTERY_BOLTZ_EXE", "boltz"),
        accelerator=os.environ.get("SMELTERY_BOLTZ_ACCELERATOR", "cpu"),
    )
    res = p.predict(SEQ)
    assert res is not None, p.last_error
    assert res.kind == "PREDICTED" and len(res.plddt) == 20 and res.license_id == "MIT"


def test_documented_layout_also_accepted(monkeypatch):
    monkeypatch.setitem(globals(), "LAYOUT", ".")
    assert BoltzProvider(make_runner(pdb=_synth_pdb(20))).predict(SEQ) is not None
