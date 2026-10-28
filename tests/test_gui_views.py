"""The GUI viewers (smeltery.gui.app and smeltery.gui.views) on the shared run fixture: every view builds headless
and draws only what the cut decided, decisions are validated and saved where the contract says, the Marimo app
runs all its cells without a browser, and the launcher refuses cleanly when an extra is missing."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from gui_fixture import make_run_dir

ROOT = Path(__file__).resolve().parent.parent
SHIM_SITE = ROOT / "tests" / "_gui_shims" / "site"


@pytest.fixture(autouse=True)
def _contract_modules(monkeypatch):
    """TEMPORARY: until smeltery.gui.results / scenes and smeltery.decisions are merged, use the shims."""
    from _gui_shims import install

    install(monkeypatch)


def _subprocess_env() -> dict:
    """Env for a child Python that sees this checkout (and the shims while the real modules are missing)."""
    paths = [str(SHIM_SITE), str(ROOT / "src"), os.environ.get("PYTHONPATH", "")]
    return {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in paths if p)}


@pytest.fixture
def run(tmp_path):
    from smeltery.gui import results

    return results.load_run(make_run_dir(tmp_path))


@pytest.fixture
def unranked_run(tmp_path):
    from smeltery.gui import results

    return results.load_run(make_run_dir(tmp_path / "unranked", unranked=True))


def _named(items, prefix: str) -> list:
    return [x for x in items if (x.name or "").startswith(prefix)]


# ---------------------------------------------------------------------------- import hygiene


def test_gui_package_imports_without_any_gui_library():
    code = (
        "import sys, smeltery.gui, smeltery.gui.app, smeltery.gui.views\n"
        "from smeltery.gui.views import builder, forest, funnel, grid, matrix, per_pose, pose\n"
        "bad = sorted(m for m in ('marimo', 'anywidget', 'mols2grid', 'plotly', 'molviewspec', 'webview')"
        " if m in sys.modules)\n"
        "assert not bad, bad\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True, env=_subprocess_env(), cwd=ROOT)


# ---------------------------------------------------------------------------- figures


def test_forest_has_one_band_per_tie_group_and_the_keep_boundary(run):
    pytest.importorskip("plotly")
    from smeltery.gui import results
    from smeltery.gui.views.forest import forest_figure

    rows = results.ddE_table(run)
    fig = forest_figure(rows, run)
    assert sum(len(t.x) for t in fig.data) == len(rows) == len(run.analogues)
    assert {t.name for t in fig.data} == {"kept by the cut", "dropped by the cut"}
    assert len(_named(fig.layout.shapes, "tie-group-")) == len(run.groups) == 2
    labels = [a.text for a in _named(fig.layout.annotations, "tie-group-")]
    assert "tie group 2 (no order)" in labels and "group 1" in labels
    assert len(_named(fig.layout.shapes, "keep-boundary")) == 1
    assert not _named(fig.layout.annotations, "unranked")
    assert not _named(fig.layout.shapes, "floor-band-")  # the fixture's floor is 0


def test_forest_on_an_unranked_cut_says_so_and_draws_no_keep_boundary(unranked_run):
    pytest.importorskip("plotly")
    from smeltery.gui import results
    from smeltery.gui.views.forest import forest_figure

    run = unranked_run
    fig = forest_figure(results.ddE_table(run), run)
    (banner,) = _named(fig.layout.annotations, "unranked")
    assert "UNRANKED" in banner.text
    assert not _named(fig.layout.shapes, "keep-boundary")
    assert {t.name for t in fig.data} == {"not cut"}
    assert len(_named(fig.layout.shapes, "tie-group-")) == len(run.groups)


def test_forest_draws_a_floor_band_on_each_group_boundary_when_the_floor_is_positive(run):
    pytest.importorskip("plotly")
    from smeltery.gui import results
    from smeltery.gui.views.forest import forest_figure

    run.record["inputs"]["floor"]["value"] = 0.5
    fig = forest_figure(results.ddE_table(run), run)
    bands = _named(fig.layout.shapes, "floor-band-")
    assert len(bands) == len(run.groups) - 1
    assert all(abs((b.x1 - b.x0) - 1.0) < 1e-12 for b in bands)


def test_matrix_is_the_resolved_predicate_with_tie_groups_outlined(run):
    pytest.importorskip("plotly")
    from smeltery.gui import results
    from smeltery.gui.views.matrix import matrix_figure

    names, mat = results.resolved_matrix(run)
    fig = matrix_figure(names, mat, results.ddE_table(run))
    (heat,) = fig.data
    assert heat.type == "heatmap" and list(heat.x) == names
    assert [[bool(v) for v in row] for row in heat.z] == mat
    assert len(_named(fig.layout.shapes, "tie-group-")) == sum(len(g) > 1 for g in run.groups) == 1


def test_per_pose_has_one_line_per_analogue_pose(run):
    pytest.importorskip("plotly")
    from smeltery.gui import results
    from smeltery.gui.views.per_pose import per_pose_figure

    table = results.per_pose_table(run)
    fig = per_pose_figure(table, run, rows=results.ddE_table(run))
    assert len(fig.data) == len(table) == len(run.analogues) * 4
    assert list(fig.data[0].x) == [run.parent, table[0]["candidate"]]


def test_funnel_has_a_bar_per_stage_and_marks_an_unranked_cut(run, unranked_run):
    pytest.importorskip("plotly")
    from smeltery.gui import results
    from smeltery.gui.views.funnel import funnel_figure

    rows = results.funnel_rows(run)
    (bars,) = funnel_figure(rows).data
    assert list(bars.y) == [r["stage"] for r in rows]
    assert bars.x[-1] == 1  # keep=1, resolved
    (bars,) = funnel_figure(results.funnel_rows(unranked_run)).data
    assert "UNRANKED" in bars.text[-1] and bars.x[-1] == 3  # nobody dropped


# ---------------------------------------------------------------------------- grid


def test_grid_has_every_candidate_with_its_group_survivor_flag_and_decision(run):
    pytest.importorskip("mols2grid")
    from smeltery.gui import results
    from smeltery.gui.views.grid import grid_html, grid_iframe, grid_records, molgrid

    recs = grid_records(run, results.ddE_table(run), {"4-Cl": {"status": "hold", "reason": "synthesis"}})
    assert [r["name"] for r in recs][0] == run.parent
    assert {r["name"] for r in recs} == set(run.candidates)
    by = {r["name"]: r for r in recs}
    assert by["4-Cl"]["group"] == by["4-Br"]["group"] == "tie group 2 (no order)"
    assert by["4-F"]["cut"] == "kept by the cut" and by["4-Br"]["cut"] == "dropped by the cut"
    assert by["4-Cl"]["decision"] == "hold (synthesis)" and by["4-F"]["decision"] == "undecided"
    g = molgrid(recs, name="test-grid")
    html = grid_html(g)
    for name in run.candidates:
        assert name in html
    assert "srcdoc=" in grid_iframe(g)


def test_grid_selection_maps_back_to_candidate_names(run):
    from smeltery.gui import results
    from smeltery.gui.views.grid import grid_records, selected_candidates

    recs = grid_records(run, results.ddE_table(run))
    assert selected_candidates({"selection": "{2: 'x', 3: 'y'}"}, recs) == [recs[2]["name"], recs[3]["name"]]
    assert selected_candidates("{}", recs) == []
    assert selected_candidates({1: "s"}, recs) == [recs[1]["name"]]


def test_unranked_grid_says_nobody_was_cut(unranked_run):
    from smeltery.gui import results
    from smeltery.gui.views.grid import grid_records

    recs = grid_records(unranked_run, results.ddE_table(unranked_run))
    assert {r["cut"] for r in recs[1:]} == {"UNRANKED: not cut"}


# ---------------------------------------------------------------------------- pose


def test_pose_page_embeds_the_scene_and_packs_local_files(run, tmp_path):
    pytest.importorskip("molviewspec")
    from smeltery.gui import scenes
    from smeltery.gui.views.pose import MOLSTAR_VERSION, bundle, pose_html

    state = scenes.pose_scene(run, "4-Cl", 1)
    same, data = bundle(state, run.out_dir)
    assert data == {} and same == state  # data: URIs are left alone
    html = pose_html(state, run.out_dir)
    assert f"molstar@{MOLSTAR_VERSION}" in html and "loadMvsData" in html

    local = {"root": {"kind": "root", "children": [{"kind": "download", "params": {"url": "poses/4-Cl.sdf"}}]}}
    packed, data = bundle(local, run.out_dir)
    (name,) = data
    assert packed["root"]["children"][0]["params"]["url"] == name and data[name].startswith(b"4-Cl pose 0")
    assert local["root"]["children"][0]["params"]["url"] == "poses/4-Cl.sdf"  # input not mutated
    with pytest.raises(FileNotFoundError, match="missing.sdf"):
        bundle({"kind": "download", "params": {"url": "poses/missing.sdf"}}, run.out_dir)


# ---------------------------------------------------------------------------- builder


def test_builder_palette_stages_and_toml(run):
    from smeltery.gui import catalog
    from smeltery.gui.views import builder

    p = builder.initial_pipeline(run)
    assert p.stage("cut").params["keep"] == run.keep
    pal = builder.palette(p)
    assert {m["key"] for r in pal for m in r["methods"]} == set(catalog())
    assert {m["state"] for r in pal for m in r["methods"] if m["key"] in p.keys()} == {"placed"}
    assert any(m["state"] == "unwired" and m["why"] for r in pal for m in r["methods"])
    text, ok = builder.toml_preview(p)  # no pocket sites in the record: the draft says why it will not run
    assert not ok and text.startswith("# DRAFT") and "pocket" in text
    assert "error" in builder.stages_html(p) and "Paired cut" in builder.stages_html(p)
    p.set("pocket", site=[{"q": 1.0, "atom": 0, "from_atom": 1, "distance": 3.0}])
    text, ok = builder.toml_preview(p)
    assert ok and "[[pocket.site]]" in text and "checks clean" in builder.stages_html(p)
    assert "Paired poses" in builder.palette_html(p)


def test_builder_starts_from_the_one_config_in_out(run):
    from smeltery.gui.views import builder

    (Path(run.out_dir) / "cfg.toml").write_text(
        'campaign = "c"\nparent = "a"\n[candidates]\na = "C"\nb = "CC"\n[cut]\nkeep = 1\nfloor = 0.0\n'
        "[pocket]\nsite = [{q = 1.0, atom = 0, from_atom = 1, distance = 3.0}]\n"
    )
    assert builder.initial_pipeline(run).campaign == "c"


def test_builder_knob_parsing(run):
    from smeltery.gui.views import builder

    p = builder.initial_pipeline(run)
    knobs, errors = builder.parse_knobs(p, "cut", {"keep": "2", "z": "", "floor": "nope"})
    assert knobs == {"keep": 2, "z": None} and errors and "floor" in errors[0]
    knobs, errors = builder.parse_knobs(p, "pocket", {"site": '[{"q": 1, "atom": 0, "from_atom": 1, "distance": 3}]'})
    assert not errors and knobs["site"][0]["distance"] == 3


def test_builder_knob_form_is_a_marimo_batch(run):
    pytest.importorskip("marimo")
    from smeltery.gui import method
    from smeltery.gui.views import builder

    form = builder.knob_widgets(builder.initial_pipeline(run), "cut")
    assert set(form.value) == {p.name for p in method("cut").params}
    assert form.value["keep"] == str(run.keep)


# ---------------------------------------------------------------------------- decisions


def test_decisions_go_to_the_contract_path_and_a_tie_choice_needs_a_note(run):
    from smeltery import decisions
    from smeltery.gui import app

    path = app.decisions_path(run)
    assert path == Path(run.out_dir) / f"decisions-{run.record['input_digest'][:12]}.json"
    ds = app.load_decisions(run)
    assert app.decision_map(ds) == {}

    same, problems = app.apply_decisions(run, ds, ["4-Cl"], "keep", "within_tie_choice", "", "mg")
    assert same is ds and problems and "note" in problems[0] and not path.exists()

    ds, problems = app.apply_decisions(
        run, ds, ["4-Cl", "4-F"], "keep", "within_tie_choice", "easier to make than 4-Br", "mg"
    )
    assert not problems and path.exists()
    saved = json.loads(path.read_text())
    assert saved["run"] == run.record["input_digest"] and saved["campaign"] == run.campaign
    assert [d["candidate"] for d in saved["decisions"]] == ["4-Cl", "4-F"]
    assert saved["decisions"][0]["smiles"] == run.candidates["4-Cl"]
    assert decisions.DecisionSet.load(path).decisions[0].note == "easier to make than 4-Br"

    ds, problems = app.apply_decisions(run, app.load_decisions(run), ["4-F"], "reject", "liability", "", "mg")
    assert not problems and app.decision_map(ds)["4-F"]["status"] == "reject"
    assert len(json.loads(path.read_text())["decisions"]) == 2  # replaced, not duplicated

    _, problems = app.apply_decisions(run, ds, [], "keep", "cut_survivor")
    assert problems
    _, problems = app.apply_decisions(run, ds, ["nope"], "keep", "cut_survivor")
    assert "not a candidate" in problems[0]


def test_an_invalid_decisions_file_is_never_overwritten(run):
    from smeltery.gui import app

    path = app.decisions_path(run)
    path.write_text("{not json")
    with pytest.raises(ValueError):
        app.load_decisions(run)
    _, problems = app.apply_decisions(run, app.new_decision_set(run), ["4-F"], "keep", "cut_survivor", "", "mg")
    assert problems and "does not validate" in problems[0]
    assert path.read_text() == "{not json"


# ---------------------------------------------------------------------------- the Marimo app


def test_app_file_passes_marimo_check():
    pytest.importorskip("marimo")
    from smeltery.gui.app import APP_FILE

    r = subprocess.run(
        [sys.executable, "-m", "marimo", "check", str(APP_FILE)], capture_output=True, text=True, cwd=ROOT
    )
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize("unranked", [False, True])
def test_app_runs_every_cell_headless(tmp_path, monkeypatch, unranked):
    for mod in ("marimo", "plotly", "mols2grid", "molviewspec", "anywidget"):
        pytest.importorskip(mod)
    from smeltery.gui.app import ENV_OUT

    out = make_run_dir(tmp_path, unranked=unranked)
    monkeypatch.setenv(ENV_OUT, str(out))
    from smeltery.gui.views.notebook import app

    _, defs = app.run()
    for name in ("results_view", "grid_panel", "decision_view", "pose_view", "pipeline_view"):
        assert defs[name] is not None, name
    assert defs["run"].unranked is unranked
    assert defs["nav"].value == "Results"


def test_app_exports_to_html_with_every_cell_run(tmp_path):
    """`marimo export html` runs the notebook in its own kernel, the way `marimo run` does."""
    pytest.importorskip("marimo")
    from smeltery.gui.app import APP_FILE

    out = make_run_dir(tmp_path)
    page = tmp_path / "app.html"
    r = subprocess.run(
        [sys.executable, "-m", "marimo", "export", "html", str(APP_FILE), "-o", str(page), "--", "--out", str(out)],
        capture_output=True,
        text=True,
        env=_subprocess_env(),
        cwd=ROOT,
        timeout=600,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "gui-fixture" in page.read_text()


# ---------------------------------------------------------------------------- the launcher


def test_main_desktop_without_pywebview_exits_2_with_the_install_hint(run, monkeypatch, capsys):
    from smeltery.gui import app

    monkeypatch.setitem(sys.modules, "webview", None)  # import webview -> ImportError, installed or not
    assert app.main([str(run.out_dir), "--desktop"]) == 2
    assert "pip install 'smeltery[desktop]'" in capsys.readouterr().err


def test_main_refuses_a_directory_without_a_run(tmp_path, capsys):
    pytest.importorskip("marimo")
    from smeltery.gui import app

    assert app.main([str(tmp_path), "--no-browser"]) == 2
    assert "run-" in capsys.readouterr().err


def test_main_serves_the_app_file_with_out_passed_through(run, monkeypatch):
    pytest.importorskip("marimo")
    from smeltery.gui import app

    seen = {}

    def fake_call(cmd, env):
        seen.update(cmd=cmd, env=env)
        return 0

    monkeypatch.setattr(app.subprocess, "call", fake_call)
    assert app.main([str(run.out_dir), "--port", "47999", "--no-browser"]) == 0
    cmd = seen["cmd"]
    assert cmd[1:4] == ["-m", "marimo", "run"] and str(app.APP_FILE) in cmd
    assert "--headless" in cmd and cmd[cmd.index("--port") + 1] == "47999"
    assert cmd[-2:] == ["--out", str(Path(run.out_dir).resolve())]
    assert seen["env"][app.ENV_OUT] == str(Path(run.out_dir).resolve())


def test_resolve_out_prefers_the_command_line(monkeypatch, tmp_path):
    from smeltery.gui import app

    monkeypatch.setenv(app.ENV_OUT, "/from/env")
    assert app.resolve_out({"out": str(tmp_path)}) == tmp_path
    assert app.resolve_out({}) == Path("/from/env")
    monkeypatch.delenv(app.ENV_OUT)
    with pytest.raises(ValueError, match=app.ENV_OUT):
        app.resolve_out(None)
