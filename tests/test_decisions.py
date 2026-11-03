"""Decisions (docs/gui-contract.md section 3): the file's rules, and `[decisions]` carrying them into the next run.
Ferric-free: the CLI tests build plans (RDKit poses) and stop before the SCFs, or stand in fixed numbers for them."""

import hashlib
import json
import re
import tomllib

import pytest

from smeltery.cli import ConfigError, build_plan, load_config, main, run_plan, write_outputs
from smeltery.decisions import REASONS, STATUSES, Decision, DecisionSet
from smeltery.gui import Pipeline
from smeltery.record import RunRecord

CANDS = {
    "benzoic": "OC(=O)c1ccccc1",
    "4-F": "OC(=O)c1ccc(F)cc1",
    "4-Cl": "OC(=O)c1ccc(Cl)cc1",
    "4-Br": "OC(=O)c1ccc(Br)cc1",
}
T = "2026-10-28T20:00:00Z"

CONFIG = """
campaign = "dec"
parent = "benzoic"

[candidates]
benzoic = "OC(=O)c1ccccc1"
4-F = "OC(=O)c1ccc(F)cc1"
4-Cl = "OC(=O)c1ccc(Cl)cc1"
4-Br = "OC(=O)c1ccc(Br)cc1"

[poses]
n_poses = 2

[[pocket.site]]
q = 1.0
atom = 0
from_atom = 1
distance = 3.0

[cut]
keep = 1
z = 2.0
floor = 0.0
"""


def entry(candidate, status="keep", reason="cut_survivor", **kw):
    return {"candidate": candidate, "smiles": CANDS[candidate], "status": status, "reason": reason, "time": T, **kw}


def write_decisions(path, *entries, **top):
    data = {"schema": 1, "campaign": "dec", "run": "0" * 64, "decisions": list(entries), **top}
    path.write_text(json.dumps(data))
    return path


# ---------------------------------------------------------------- the file


def test_set_stamps_utc_time_and_replaces_in_place(tmp_path):
    ds = DecisionSet("dec", "abc", candidates=CANDS)
    ds.set("4-F", "keep", "cut_survivor", user="mg")
    ds.set("4-Cl", "hold", "synthesis")
    d = ds.set("4-F", "reject", "liability", "hERG flag", "mg")
    assert [x.candidate for x in ds.decisions] == ["4-F", "4-Cl"]  # replaced in its place, not appended
    assert ds.get("4-F") == d and d.status == "reject" and d.smiles == CANDS["4-F"]
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", d.time)
    path = ds.dump(tmp_path / "d.json")
    back = DecisionSet.load(path)
    assert back.decisions == ds.decisions and (back.campaign, back.run, back.schema) == ("dec", "abc", 1)
    assert json.loads(path.read_text())["decisions"][0] == {
        "candidate": "4-F",
        "smiles": CANDS["4-F"],
        "status": "reject",
        "reason": "liability",
        "note": "hERG flag",
        "user": "mg",
        "time": d.time,
    }
    assert ds.by_status("reject") == ["4-F"] and ds.by_status("hold") == ["4-Cl"]


def test_set_needs_a_smiles_and_refuses_an_invalid_entry_without_changing_anything():
    ds = DecisionSet("dec", "abc")
    with pytest.raises(ValueError, match="no SMILES for '4-F'"):
        ds.set("4-F", "keep", "cut_survivor")
    ds.set("4-F", "keep", "cut_survivor", smiles=CANDS["4-F"])
    ds.set("4-F", "hold", "novelty")  # the existing entry's SMILES is reused
    with pytest.raises(ValueError, match="needs a note"):
        ds.set("4-F", "keep", "within_tie_choice")
    with pytest.raises(ValueError, match="status 'maybe'"):
        ds.set("4-F", "maybe", "other", "x")
    assert ds.get("4-F").status == "hold"


def test_the_contract_vocabulary():
    assert STATUSES == ("keep", "reject", "hold")
    assert set(REASONS) == {
        "cut_survivor",
        "within_tie_choice",
        "liability",
        "synthesis",
        "pose_implausible",
        "novelty",
        "other",
    }


@pytest.mark.parametrize(
    ("entries", "top", "message"),
    [
        ([entry("4-F", status="maybe")], {}, "status 'maybe' is not one of"),
        ([entry("4-F", reason="vibes")], {}, "reason 'vibes' is not one of"),
        ([entry("4-F", reason="other")], {}, "needs a note"),
        ([entry("4-F", reason="within_tie_choice", note="  ")], {}, "needs a note"),
        ([entry("4-F"), entry("4-F", status="reject", reason="liability")], {}, "more than one decision"),
        ([entry("4-F", smiles="")], {}, "smiles is required"),
        ([entry("4-F", smiles="C(((")], {}, "unparseable SMILES"),
        ([entry("4-F", time="yesterday")], {}, "not UTC"),
        ([entry("4-F", colour="red")], {}, "unknown keys \\['colour'\\]"),
        ([{k: v for k, v in entry("4-F").items() if k != "smiles"}], {}, "missing \\['smiles'\\]"),
        ([entry("4-F")], {"schema": 2}, "schema 2 is not supported"),
        ([entry("4-F")], {"extra": 1}, "unknown keys \\['extra'\\]"),
    ],
)
def test_validation_refuses_with_a_clear_message(tmp_path, entries, top, message):
    path = write_decisions(tmp_path / "d.json", *entries, **top)
    with pytest.raises(ValueError, match=message):
        DecisionSet.load(path)


def test_validate_reports_every_problem_at_once_and_dump_writes_nothing_when_invalid(tmp_path):
    ds = DecisionSet("dec", "abc", [Decision("4-F", CANDS["4-F"], "maybe", "other", time=T)])
    with pytest.raises(ValueError) as e:
        ds.validate()
    assert "status 'maybe'" in str(e.value) and "needs a note" in str(e.value)
    with pytest.raises(ValueError):
        ds.dump(tmp_path / "d.json")
    assert not (tmp_path / "d.json").exists()


def test_an_unreadable_file_names_its_path(tmp_path):
    p = tmp_path / "d.json"
    p.write_text("{not json")
    with pytest.raises(ValueError, match="cannot read decisions file"):
        DecisionSet.load(p)


# ---------------------------------------------------------------- [decisions] in a run


def config(tmp_path, *entries, file="decisions.json", extra=""):
    write_decisions(tmp_path / "decisions.json", *entries)
    p = tmp_path / "c.toml"
    p.write_text(CONFIG + f'\n[decisions]\nfile = "{file}"\n' + extra)
    return p


def digest(plan):
    return RunRecord(plan.cfg["campaign"], plan.inputs, plan.tiers, {}).input_digest


def test_a_reject_is_dropped_before_posing_and_hold_and_keep_run(tmp_path):
    p = config(tmp_path, entry("4-Cl", "reject", "liability"), entry("4-F", "hold", "synthesis"), entry("4-Br"))
    plan = build_plan(load_config(p))
    assert [c.name for c in plan.candidates] == ["benzoic", "4-F", "4-Br"]
    assert all(c.poses for c in plan.candidates)
    assert plan.decisions_in["excluded"] == ["4-Cl"]
    assert plan.decisions_in["file"] == str((tmp_path / "decisions.json").resolve())  # relative to the config dir
    assert "4-Cl" not in plan.inputs["candidates"]


def test_decisions_in_is_recorded_relative_to_out_or_absolute(tmp_path):
    p = config(tmp_path, entry("4-Cl", "reject", "liability"))
    plan = build_plan(load_config(p))

    def fill(pl):
        for c in pl.candidates:
            c.per_pose["dE_int"] = [float(i) + 0.1 * len(c.name) for i in range(len(c.poses))]

    rec, _ = run_plan(plan, measure=fill)
    path = write_outputs(plan, rec, tmp_path / "out")
    res = json.loads(path.read_text())["results"]
    sha = hashlib.sha256((tmp_path / "decisions.json").read_bytes()).hexdigest()
    assert res["decisions_in"] == {
        "file": str((tmp_path / "decisions.json").resolve()),  # outside OUT: absolute
        "sha256": sha,
        "excluded": ["4-Cl"],
    }
    assert res["candidate_order"] == ["benzoic", "4-F", "4-Br"]
    assert "4-Cl" not in res["per_pose"] and "4-Cl" not in res["poses"]

    inside = tmp_path / "out2"
    inside.mkdir()
    write_decisions(inside / "d.json", entry("4-Cl", "reject", "liability"))
    p = tmp_path / "c2.toml"
    p.write_text(CONFIG + '\n[decisions]\nfile = "out2/d.json"\n')
    plan = build_plan(load_config(p))
    rec, _ = run_plan(plan, measure=fill)
    res = json.loads(write_outputs(plan, rec, inside).read_text())["results"]
    assert res["decisions_in"]["file"] == "d.json"


def test_rejecting_the_parent_is_a_config_error_exit_2(tmp_path, capsys):
    p = config(tmp_path, entry("benzoic", "reject", "other", note="wrong series"))
    assert main(["run", str(p), "--plan"]) == 2
    assert "rejects the parent 'benzoic'" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("entries", "message"),
    [
        ([{**entry("4-F"), "candidate": "4-I", "smiles": "OC(=O)c1ccc(I)cc1"}], r"decides on \['4-I'\]"),
        ([{**entry("4-F"), "smiles": "OC(=O)c1ccc(Cl)cc1"}], "a different molecule under the same name"),
        (
            [entry(n, "reject", "liability") for n in ("4-F", "4-Cl", "4-Br")],
            "leaving 0 analogue",
        ),
        ([entry("4-F", "maybe")], "status 'maybe'"),
    ],
)
def test_a_decisions_file_that_does_not_fit_the_config_is_a_config_error(tmp_path, entries, message):
    p = config(tmp_path, *entries)
    with pytest.raises(ConfigError, match=message):
        build_plan(load_config(p))


def test_keep_larger_than_what_is_left_is_a_config_error(tmp_path):
    p = config(tmp_path, entry("4-F", "reject", "liability"), entry("4-Cl", "reject", "liability"))
    text = p.read_text().replace("keep = 1", "keep = 2")
    p.write_text(text)
    with pytest.raises(ConfigError, match=r"leaving 1 analogue\(s\); \[cut\] keep = 2"):
        build_plan(load_config(p))


def test_an_equivalent_smiles_spelling_is_not_a_mismatch(tmp_path):
    p = config(tmp_path, {**entry("4-F", "reject", "liability"), "smiles": "Fc1ccc(cc1)C(O)=O"})
    assert [c.name for c in build_plan(load_config(p)).candidates] == ["benzoic", "4-Cl", "4-Br"]


@pytest.mark.parametrize(
    ("section", "message"),
    [
        ('[decisions]\nfil = "d.json"\n', "unknown key"),
        ("[decisions]\n", "file is required"),
        ('[decisions]\nfile = "missing.json"\n', "does not exist"),
    ],
)
def test_a_bad_decisions_section_is_a_config_error(tmp_path, section, message):
    p = tmp_path / "c.toml"
    p.write_text(CONFIG + "\n" + section)
    with pytest.raises(ConfigError, match=message):
        build_plan(load_config(p))


def test_the_digest_changes_with_the_decisions_and_not_without_the_section(tmp_path):
    base = tmp_path / "base.toml"
    base.write_text(CONFIG)
    plain = build_plan(load_config(base))
    assert "decisions" not in plain.inputs  # a config without [decisions] has the inputs (and digest) it had before
    assert set(plain.inputs) == {"candidates", "parent", "cut", "floor", "gates", "field"}

    keep_only = build_plan(load_config(config(tmp_path, entry("4-F"))))
    assert {k: v for k, v in keep_only.inputs.items() if k != "decisions"} == plain.inputs
    assert keep_only.inputs["decisions"] == {"sha256": keep_only.decisions_in["sha256"]}
    assert digest(keep_only) != digest(plain)

    held = build_plan(load_config(config(tmp_path, entry("4-F", "hold", "synthesis"))))
    assert digest(held) != digest(keep_only)  # same candidates, different file: a different run


def test_the_pipeline_carries_a_decisions_table_through_unchanged(tmp_path):
    cfg = tomllib.loads(CONFIG + '\n[decisions]\nfile = "decisions.json"\n')
    p = Pipeline.from_config(cfg)
    assert p.decisions == {"file": "decisions.json"}
    assert p.to_config() == cfg
    assert tomllib.loads(p.to_toml()) == cfg  # check() passes: validate_config accepts the section
    assert "decisions" not in Pipeline.from_config(tomllib.loads(CONFIG)).to_config()
