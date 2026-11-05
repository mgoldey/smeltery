"""The workspace page's routes: steps of a draft, in-place step edits that keep the user's text, and Run."""

import json
import tomllib
from pathlib import Path

from bench.fakes import FakeTaskQueue

HAND = (
    "# the benzoic series, hand-placed charges\n"
    'campaign = "benzoic"\n'
    'parent = "benzoic"\n'
    "\n"
    "[candidates]\n"
    'benzoic = "OC(=O)c1ccccc1"\n'
    '4-F = "OC(=O)c1ccc(F)cc1"\n'
    "\n"
    "[poses]\n"
    "n_poses = 6  # poses per ligand\n"
    "seed = 7\n"
    "\n"
    "# one charge beyond the acid\n"
    "[[pocket.site]]\n"
    "q = 1.0\n"
    "atom = 1\n"
    "from_atom = 2\n"
    "distance = 3.0\n"
    "\n"
    "[cut]\n"
    "keep = 1\n"
    "z = 2.0\n"
    "floor = 0.0\n"
)


def _steps(c, toml, baseline=None):
    r = c.post("/api/pipelines/steps", json={"toml": toml, "baseline_toml": baseline})
    assert r.status_code == 200, r.text
    return r.json()


def _by_key(body):
    return {s["key"]: s for s in body["steps"]}


def _edit(c, toml, op, key, **extra):
    return c.post("/api/pipelines/edit", json={"toml": toml, "op": op, "key": key, **extra})


def _changed(old: str, new: str) -> list[tuple[str, str]]:
    return [(a, b) for a, b in zip(old.splitlines(), new.splitlines(), strict=False) if a != b]


# ------------------------------------------------------------------------------------------------ steps


def test_steps_mark_the_edited_step_its_old_value_and_the_later_steps(client_for):
    c, _ = client_for()
    edited = HAND.replace("n_poses = 6", "n_poses = 8")
    body = _steps(c, edited, HAND)
    steps = _by_key(body)
    assert [s["key"] for s in body["steps"]] == ["active_site", "ligands", "poses", "checks", "measure", "cut"]
    assert steps["poses"]["state"] == "changed"
    n = next(p for p in steps["poses"]["params"] if p["name"] == "n_poses")
    assert n["value"] == 8 and n["changed_from"] == 6 and n["help"]
    assert steps["measure"]["state"] == "stale" and steps["cut"]["state"] == "stale"
    assert steps["active_site"]["state"] == "ready" and steps["checks"]["state"] == "off"
    assert body["ligands"] == {
        "campaign": "benzoic",
        "parent": "benzoic",
        "candidates": {"benzoic": "OC(=O)c1ccccc1", "4-F": "OC(=O)c1ccc(F)cc1"},
    }
    # the diff is the real line diff of the text, comment kept
    changed = [d for d in body["diff"] if d["sign"] != " "]
    assert changed == [
        {"sign": "-", "line": "n_poses = 6  # poses per ligand"},
        {"sign": "+", "line": "n_poses = 8  # poses per ligand"},
    ]


def test_steps_without_a_baseline_change_nothing(client_for):
    c, _ = client_for()
    body = _steps(c, HAND)
    assert {s["state"] for s in body["steps"]} <= {"ready", "off"}
    assert all(d["sign"] == " " for d in body["diff"])


def test_steps_put_an_issue_on_its_step_and_the_rest_above(client_for):
    c, _ = client_for()
    no_site = HAND[: HAND.index("# one charge")] + HAND[HAND.index("[cut]") :]
    no_site = no_site.replace("[cut]", "[pocket]\n\n[cut]")
    body = _steps(c, no_site)
    site = _by_key(body)["active_site"]
    assert site["state"] == "needs_input"
    assert any("needs at least one charge" in i["message"] for i in site["issues"])
    assert all("needs at least one charge" not in i["message"] for i in body["pipeline_issues"])


def test_steps_on_unparseable_toml_is_400_with_the_line(client_for):
    c, _ = client_for()
    r = c.post("/api/pipelines/steps", json={"toml": 'campaign = "x"\nbad = = 1\n'})
    assert r.status_code == 400 and "line 2" in r.json()["detail"]
    r = c.post("/api/pipelines/steps", json={"toml": HAND, "baseline_toml": "x = \n"})
    assert r.status_code == 400 and "line 1" in r.json()["detail"]


def test_steps_carry_the_workspace_default_beside_each_value(client_for):
    c, _ = client_for()
    assert c.put("/api/methods/paired_poses", json={"defaults": {"n_poses": 12}}).status_code == 200
    n = next(p for p in _by_key(_steps(c, HAND))["poses"]["params"] if p["name"] == "n_poses")
    assert n["value"] == 6 and n["workspace_default"] == 12


def test_a_toml_that_is_not_a_pipeline_is_an_issue_not_a_500(client_for):
    c, _ = client_for()
    body = _steps(c, 'campaign = "x"\ncandidates = 3\n')
    assert body["steps"] == [] and body["ligands"] is None and body["pipeline_issues"][0]["level"] == "error"


# ------------------------------------------------------------------------------------------------ edits


def test_setting_a_knob_changes_only_its_line_and_keeps_the_comments(client_for):
    c, _ = client_for()
    r = _edit(c, HAND, "set", "paired_poses", params={"n_poses": 8})
    assert r.status_code == 200, r.text
    text = r.json()["toml"]
    assert _changed(HAND, text) == [("n_poses = 6  # poses per ligand", "n_poses = 8  # poses per ligand")]
    assert len(text.splitlines()) == len(HAND.splitlines())


def test_unsetting_a_knob_removes_its_line_only(client_for):
    c, _ = client_for()
    text = _edit(c, HAND, "set", "paired_poses", params={"seed": None}).json()["toml"]
    assert text == HAND.replace("seed = 7\n", "")


def test_an_edit_does_not_rewrite_what_it_did_not_touch(client_for):
    # an explicit empty [field_interaction] that Pipeline.to_config() would drop (RHF is required, so implicit)
    c, _ = client_for()
    odd = HAND.replace("[cut]", "# RHF, the default\n[field_interaction]\n\n[cut]")
    text = _edit(c, odd, "set", "paired_poses", params={"n_poses": 9}).json()["toml"]
    assert "# RHF, the default\n[field_interaction]\n" in text
    assert _changed(odd, text) == [("n_poses = 6  # poses per ligand", "n_poses = 9  # poses per ligand")]


def test_adding_a_method_copies_the_workspace_default(client_for):
    c, _ = client_for()
    assert c.put("/api/methods/forcefield", json={"defaults": {"max_strain_kcal": 9.5}}).status_code == 200
    text = _edit(c, HAND, "add", "forcefield").json()["toml"]
    assert text.startswith(HAND)  # everything that was there is as it was
    assert tomllib.loads(text)["gates"] == {"forcefield": True, "max_strain_kcal": 9.5}


def test_removing_a_method_removes_its_lines(client_for):
    c, _ = client_for()
    added = _edit(c, HAND, "add", "gfn2").json()["toml"]
    assert "xtb = true" in added
    assert _by_key(_steps(c, added))["checks"]["placed"] == ["gfn2"]
    back = _edit(c, added, "remove", "gfn2").json()["toml"]
    assert "xtb = true" not in back and "# poses per ligand" in back


def test_removing_the_only_measurement_is_refused_with_its_alternative(client_for):
    c, _ = client_for()
    r = _edit(c, HAND, "remove", "field_interaction")
    assert r.status_code == 400 and "field_dft_interaction" in r.json()["detail"]


def test_choosing_ks_dft_replaces_rhf_in_the_measure_step(client_for):
    c, _ = client_for()
    r = _edit(c, HAND, "choose", "field_dft_interaction")
    assert r.status_code == 200, r.text
    text = r.json()["toml"]
    assert "[field_dft_interaction]" in text and "[field_interaction]" not in text
    steps = _by_key(_steps(c, text, HAND))
    assert steps["measure"]["chosen"] == "field_dft_interaction" and steps["measure"]["state"] == "changed"
    back = _edit(c, text, "choose", "field_interaction").json()["toml"]
    assert "[field_dft_interaction]" not in back


def test_placing_a_site_appends_one_block_and_keeps_the_others(client_for):
    c, _ = client_for()
    r = _edit(c, HAND, "site", "pocket", site={"atom": 3, "from_atom": 4, "q": -1.0, "distance": 3.5})
    assert r.status_code == 200, r.text
    text = r.json()["toml"]
    assert text.count("[[pocket.site]]") == 2 and "# one charge beyond the acid" in text
    sites = tomllib.loads(text)["pocket"]["site"]
    assert len(sites) == 2 and sites[0] == {"q": 1.0, "atom": 1, "from_atom": 2, "distance": 3.0}
    assert {k: sites[1][k] for k in ("atom", "from_atom", "q", "distance")} == {
        "atom": 3,
        "from_atom": 4,
        "q": -1.0,
        "distance": 3.5,
    }


def test_an_edit_on_unparseable_text_is_400_with_the_line(client_for):
    c, _ = client_for()
    r = _edit(c, HAND + "\nbad = = 1\n", "set", "paired_poses", params={"n_poses": 8})
    assert r.status_code == 400 and f"line {len(HAND.splitlines()) + 2}" in r.json()["detail"]


def test_an_unknown_knob_or_method_is_400(client_for):
    c, _ = client_for()
    assert _edit(c, HAND, "set", "paired_poses", params={"nope": 1}).status_code == 400
    assert _edit(c, HAND, "add", "no_such_method").status_code == 400
    assert _edit(c, HAND, "site", "pocket").status_code == 400  # a site edit needs the site


# ------------------------------------------------------------------------------------------------ run


def _saved(c, name, text):
    made = c.post(
        "/api/pipelines", json={"name": name, "blank": {"campaign": "x", "parent": "a", "candidates": {"a": "CCO"}}}
    )
    assert made.status_code == 201, made.text
    r = c.put(f"/api/pipelines/{name}", json={"toml": text, "base_sha256": made.json()["sha256"]})
    assert r.status_code == 200, r.text
    return r.json()


def test_run_is_refused_with_the_error_count_while_check_has_errors(client_for):
    queue = FakeTaskQueue()
    c, _ = client_for(queue=queue)
    bad = HAND.replace("keep = 1", 'keep = "many"').replace("n_poses = 6", 'n_poses = "lots"')
    doc = _saved(c, "p1", bad)
    errors = [i for i in doc["issues"] if i["level"] == "error"]
    assert errors
    r = c.post("/api/pipelines/p1/launch", json={})
    assert r.status_code == 409
    reason = r.json()["reason"]
    assert reason.startswith(f"{len(errors)} error") and errors[0]["message"] in reason
    assert queue.submitted == []


def test_run_launches_the_saved_config_from_its_folder(client_for, tmp_path):
    queue = FakeTaskQueue()
    c, ports = client_for(queue=queue)
    doc = _saved(c, "p1", HAND)
    assert doc["ok"], doc["issues"]
    out = tmp_path / "runs" / "one"
    r = c.post("/api/pipelines/p1/launch", json={"out": str(out)})
    assert r.status_code == 201, r.text
    (name, command, cwd, _note) = queue.submitted[0]
    assert command[-3:] == [str(out / "config.toml"), "--out", str(out)]
    written = tomllib.loads((out / "config.toml").read_text())
    assert written["poses"]["n_poses"] == 6 and written["pocket"]["site"][0]["atom"] == 1
    assert r.json()["id"] == "t0"


def test_run_resolves_relative_inputs_against_the_pipeline_folder(client_for, tmp_path):
    queue = FakeTaskQueue()
    c, _ = client_for(queue=queue)
    doc = _saved(c, "p1", HAND)
    folder = Path(doc["folder"])
    (folder / "decisions.toml").write_text("")
    text = HAND + '\n[decisions]\nfile = "decisions.toml"\n'
    doc = c.put("/api/pipelines/p1", json={"toml": text, "base_sha256": doc["sha256"]}).json()
    out = tmp_path / "runs" / "two"
    r = c.post("/api/pipelines/p1/launch", json={"out": str(out)})
    assert r.status_code == 201, r.text
    staged = tomllib.loads((out / "config.toml").read_text())["decisions"]["file"]
    assert staged.startswith("inputs/")
    assert (out / staged).is_file()


def test_run_of_an_unknown_pipeline_is_404(client_for):
    c, _ = client_for()
    assert c.post("/api/pipelines/ghost/launch", json={}).status_code == 404


# ------------------------------------------------------------------------------------------------ create (R15)


def test_a_blank_pipeline_picks_up_the_workspace_defaults(client_for):
    c, _ = client_for()
    assert c.put("/api/methods/paired_poses", json={"defaults": {"n_poses": 12}}).status_code == 200
    made = c.post(
        "/api/pipelines", json={"name": "p1", "blank": {"campaign": "x", "parent": "a", "candidates": {"a": "CCO"}}}
    )
    assert made.status_code == 201, made.text
    assert tomllib.loads(made.json()["toml"])["poses"]["n_poses"] == 12


def test_from_a_run_keeps_the_runs_value_whatever_the_workspace_default(client_for, run_dir):
    (run_dir / "config.toml").write_text(HAND)
    rec_path = next(run_dir.glob("run-*.json"))
    rec = json.loads(rec_path.read_text())
    rec["results"]["config"] = {"file": "config.toml", "sha256": "0" * 64, "inputs": {}}
    rec_path.write_text(json.dumps(rec))
    c, _ = client_for(run_dir)
    assert c.put("/api/methods/paired_poses", json={"defaults": {"n_poses": 12}}).status_code == 200
    made = c.post("/api/pipelines", json={"name": "next", "from_run": 0})
    assert made.status_code == 201, made.text
    assert made.json()["toml"] == HAND  # n_poses = 6 as the run had it, byte for byte
