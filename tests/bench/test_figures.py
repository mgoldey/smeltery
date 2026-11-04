"""The figures API serves exactly what smeltery.gui.views builds; the browser only draws it.

Data source: the builders called directly on load_run(fixture) in the test, compared with the response.
"""

import json

import pytest

from smeltery.gui.results import ddE_table, load_run, per_pose_table, resolved_matrix
from smeltery.gui.views.forest import forest_figure
from smeltery.gui.views.matrix import matrix_figure
from smeltery.gui.views.per_pose import per_pose_figure


def _expected(name, run_dir):
    r = load_run(run_dir)
    table = ddE_table(r)
    unit = r.record["results"]["units"]["dE_int"]
    if name == "forest":
        fig = forest_figure(table, r, unit=unit)
    elif name == "matrix":
        fig = matrix_figure(*resolved_matrix(r), table)
    else:
        fig = per_pose_figure(per_pose_table(r, "dE_int"), r, quantity="dE_int", unit=unit, rows=table)
    return json.loads(fig.to_json())


@pytest.mark.parametrize("name", ["forest", "matrix", "per_pose"])
def test_each_figure_is_the_builders_output(client_for, run_dir, name):
    c, _ = client_for(run_dir)
    r = c.get(f"/api/runs/0/figures/{name}")
    assert r.status_code == 200
    assert r.json() == _expected(name, run_dir)


def test_the_forest_has_one_point_per_analogue_and_a_floor_band(client_for, run_dir):
    c, _ = client_for(run_dir)
    fig = c.get("/api/runs/0/figures/forest").json()
    rows = c.get("/api/runs/0/ddE").json()["rows"]
    assert fig["layout"]["yaxis"]["ticktext"] == [r["candidate"] for r in rows]
    assert sum(len(tr.get("y") or []) for tr in fig["data"] if tr.get("mode") == "markers") == len(rows)
    assert fig["layout"].get("shapes"), "the tie bands are layout shapes"
    labels = [a.get("text", "") for a in fig["layout"].get("annotations", [])]
    assert any("tie group" in t for t in labels), labels


def test_an_unknown_figure_is_404(client_for, run_dir):
    c, _ = client_for(run_dir)
    r = c.get("/api/runs/0/figures/pie")
    assert r.status_code == 404 and "forest" in r.json()["detail"]


def test_an_incomplete_run_has_no_figures_409(client_for, incomplete_run_dir):
    c, _ = client_for(incomplete_run_dir)
    r = c.get("/api/runs/0/figures/forest")
    assert r.status_code == 409 and "cut" in r.json()["detail"]


def _strings(x):
    if isinstance(x, dict):
        for v in x.values():
            yield from _strings(v)
    elif isinstance(x, list):
        for v in x:
            yield from _strings(v)
    elif isinstance(x, str):
        yield x.lower()


@pytest.mark.parametrize("name", ["forest", "matrix", "per_pose"])
def test_a_dark_figure_has_no_light_chrome_and_keeps_its_data_colours(client_for, run_dir, name):
    """Data source: the palette in smeltery.gui.views._style, which owns both themes."""
    from smeltery.gui.views import _style

    c, _ = client_for(run_dir)
    light = c.get(f"/api/runs/0/figures/{name}").json()
    dark = c.get(f"/api/runs/0/figures/{name}", params={"theme": "dark"}).json()
    seen_light, seen_dark = set(_strings(light)), set(_strings(dark))
    chrome = {k.lower() for k in _style.DARK}
    assert seen_light & chrome, "the light figure uses the light chrome, or this test checks nothing"
    assert not (seen_dark & chrome), sorted(seen_dark & chrome)
    data = {v.lower() for v in (_style.KEPT, _style.NOT_KEPT, _style.UNRANKED)}
    assert seen_light & data == seen_dark & data
    # only colours change: the traces' numbers are the same figure
    assert [t.get("x") for t in dark["data"]] == [t.get("x") for t in light["data"]]


def test_the_default_theme_is_light_and_an_unknown_one_is_refused(client_for, run_dir):
    c, _ = client_for(run_dir)
    assert c.get("/api/runs/0/figures/forest").json() == c.get("/api/runs/0/figures/forest?theme=light").json()
    assert c.get("/api/runs/0/figures/forest?theme=sepia").status_code == 422
