import hashlib
import json

import pytest
from gui_fixture import PARENT, make_run_dir
from rdkit import Chem

from smeltery import cli
from smeltery.decisions import DecisionSet
from smeltery.gui import results as R
from smeltery.sdf_export import enriched_sdf, load_decisions


def records(text):
    sup = Chem.SDMolSupplier()
    sup.SetData(text, removeHs=False, sanitize=False)
    return [m for m in sup]


def props(m):
    return {k: m.GetProp(k) for k in m.GetPropNames()}


@pytest.fixture
def run_dir(tmp_path):
    return make_run_dir(tmp_path)


def test_every_pose_keeps_its_properties_and_gains_the_cut_results(run_dir):
    run = R.load_run(run_dir)
    recs = records(enriched_sdf(run))
    total = sum(e["n_poses"] for e in run.record["results"]["poses"].values())
    assert len(recs) == total
    paired = run.record["results"]["paired_ddE"]
    for m in recs:
        p = props(m)
        c = p["smeltery_candidate"]
        assert "dE_int" in p and "smeltery_pose_index" in p
        assert "ddE=kcal/mol" in p["smeltery_units"]
        if c == PARENT:
            assert "smeltery_ddE" not in p and "smeltery_group" not in p
            continue
        assert float(p["smeltery_ddE"]) == pytest.approx(paired[c]["mean"])
        assert float(p["smeltery_ddE_sem"]) == pytest.approx(paired[c]["sem"])
        grp = [i for i, g in enumerate(run.groups, 1) if c in g]
        assert p["smeltery_group"] == str(grp[0])
        assert p["smeltery_survivor"] == ("1" if c in run.survivors else "0")
    assert not any(k.startswith("smeltery_gate_") or "decision" in k for m in recs for k in props(m))


def test_the_run_pose_files_are_not_modified(run_dir):
    run = R.load_run(run_dir)
    before = {n: (run_dir / e["file"]).read_bytes() for n, e in run.record["results"]["poses"].items()}
    enriched_sdf(run)
    for n, e in run.record["results"]["poses"].items():
        assert (run_dir / e["file"]).read_bytes() == before[n]
        assert hashlib.sha256(before[n]).hexdigest() == e["sha256"]


def test_unranked_cut_has_no_survivor_flag(tmp_path):
    run = R.load_run(make_run_dir(tmp_path, unranked=True))
    recs = records(enriched_sdf(run))
    assert all("smeltery_survivor" not in props(m) for m in recs)
    assert any(props(m).get("smeltery_unranked") == "1" for m in recs)


def test_decisions_and_a_multiline_note_stay_on_their_candidate(run_dir):
    run = R.load_run(run_dir)
    ds = DecisionSet(campaign=run.campaign, run=run.record["input_digest"], candidates=dict(run.candidates))
    ds.set("4-F", "keep", "other", "first\n\nsecond\nthird", user="mg")
    ds.dump(run_dir / f"decisions-{run.record['input_digest'][:12]}.json")
    run = R.load_run(run_dir)
    recs = records(enriched_sdf(run, load_decisions(run)))
    decided = [props(m) for m in recs if props(m)["smeltery_candidate"] == "4-F"]
    assert decided and all(p["smeltery_decision_note"] == "first second third" for p in decided)
    assert all(p["smeltery_decision_status"] == "keep" and p["smeltery_decision_user"] == "mg" for p in decided)
    assert all("smeltery_decision_status" not in props(m) for m in recs if props(m)["smeltery_candidate"] != "4-F")


def add_gates(run_dir):
    p = next(run_dir.glob("run-*.json"))
    rec = json.loads(p.read_text())
    res = rec["results"]
    n = rec["results"]["poses"][PARENT]["n_poses"]
    res["gates"] = {
        "posebusters": {
            c: {"poses": [{"passed": i != 0, "failed_checks": [] if i else ["bond_lengths"]} for i in range(n)]}
            for c in res["poses"]
        },
        "strain_kcal": {c: [1.5 + i for i in range(n)] for c in res["poses"]},
    }
    p.write_text(json.dumps(rec))  # no self-hash recheck on read


def test_gate_results_become_per_pose_properties(run_dir):
    add_gates(run_dir)
    run = R.load_run(run_dir)
    by_cand = {}
    for m in records(enriched_sdf(run)):
        p = props(m)
        by_cand.setdefault(p["smeltery_candidate"], []).append(p)
    for rows in by_cand.values():
        assert rows[0]["smeltery_gate_posebusters_pass"] == "0"
        assert rows[0]["smeltery_gate_posebusters_failed_checks"] == "bond_lengths"
        assert rows[1]["smeltery_gate_posebusters_pass"] == "1"
        assert float(rows[1]["smeltery_gate_strain_kcal"]) == pytest.approx(2.5)
    assert not any("vina" in k or "xtb" in k for rows in by_cand.values() for k in rows[0])


def test_cli_writes_the_file_and_a_non_run_fails_cleanly(run_dir, tmp_path, capsys):
    dest = tmp_path / "x.sdf"
    assert cli.main(["export-sdf", str(run_dir), "--out", str(dest)]) == 0
    assert len(records(dest.read_text())) > 0
    assert cli.main(["export-sdf", str(tmp_path / "nope")]) == 2
    assert "export-sdf" in capsys.readouterr().err


def test_bench_route_serves_it(run_dir):
    from bench.fakes import FakeIdentity, FakeRunStore, FakeTaskQueue, FixedClock
    from fastapi.testclient import TestClient

    from smeltery.bench.app import create_app

    c = TestClient(
        create_app(
            run_store=FakeRunStore(run_dir), task_queue=FakeTaskQueue(), identity=FakeIdentity("mg"), clock=FixedClock()
        )
    )
    c.post("/api/runs/0/decisions", json={"candidate": "4-F", "status": "keep", "reason": "other", "note": "a\nb"})
    r = c.get("/api/runs/0/export.sdf")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    assert any(props(m).get("smeltery_decision_note") == "a b" for m in records(r.text))


def test_units_come_from_the_record(run_dir):
    run = R.load_run(run_dir)
    assert props(records(enriched_sdf(run))[0])["smeltery_units"] == "ddE=kcal/mol; dE_int=kcal/mol"


def test_gates_follow_original_pose_index_after_a_pairwise_drop(run_dir):
    p = next(run_dir.glob("run-*.json"))
    rec = json.loads(p.read_text())
    res = rec["results"]
    n = res["poses"][PARENT]["n_poses"]
    assert n >= 4
    # gates ran on every original pose; the record kept only originals 1..n-1 (SDF records still n: slice by index)
    keep = list(range(1, n))
    res["gates"] = {"strain_kcal": {c: [10.0 * k for k in range(n)] for c in res["poses"]}}
    res["pose_index"] = {c: keep + [keep[-1]] * (n - len(keep)) for c in res["poses"]}
    p.write_text(json.dumps(rec))
    got = [float(props(m)["smeltery_gate_strain_kcal"]) for m in records(enriched_sdf(R.load_run(run_dir)))]
    per = n
    assert got[:per] == [10.0 * k for k in res["pose_index"][PARENT]]
