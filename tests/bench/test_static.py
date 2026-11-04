import pathlib

import pytest
from fastapi.testclient import TestClient
from gui_fixture import make_run_dir

from bench.fakes import FakeArtifactStore, FakeIdentity, FakeRunStore, FakeTaskQueue, FixedClock
from smeltery.bench.app import create_app


@pytest.fixture
def run_dir(tmp_path):
    return make_run_dir(tmp_path)


def fakes():
    rd = pathlib.Path("/tmp") / "x"
    rd.mkdir(parents=True, exist_ok=True)
    return dict(
        run_store=FakeRunStore(rd),
        task_queue=FakeTaskQueue(),
        identity=FakeIdentity("mg"),
        clock=FixedClock(),
        artifact_store=FakeArtifactStore(pathlib.Path("/tmp") / ".art"),
    )


def test_static_files_are_served():
    c = TestClient(create_app(**fakes()))
    assert c.get("/static/app.css").status_code == 200


def test_no_page_references_an_external_origin():
    import pathlib
    import re

    root = pathlib.Path("src/smeltery/bench")
    for p in root.rglob("*"):
        if p.suffix in {".html", ".jinja", ".jinja2", ".tmpl"}:
            text = p.read_text(errors="ignore")
            assert not re.search(r"https?://", text), f"external origin in {p}"


def test_the_data_colours_match_the_scene_builder():
    import smeltery.gui.scenes as scenes

    css = (pathlib.Path("src/smeltery/bench/static/app.css")).read_text()
    for name in ("CHARGE_POSITIVE", "CHARGE_NEGATIVE", "CANDIDATE_CARBON"):
        val = getattr(scenes, name)
        assert val.lower() in css.lower()


def test_dark_mode_is_defined_for_every_colour_token():
    css = (pathlib.Path("src/smeltery/bench/static/app.css")).read_text()
    assert "@media (prefers-color-scheme: dark)" in css or '[data-theme="dark"]' in css


def test_numeric_cells_use_tabular_figures():
    css = (pathlib.Path("src/smeltery/bench/static/app.css")).read_text()
    assert "font-variant-numeric: tabular-nums" in css


def test_reduced_motion_is_respected():
    css = (pathlib.Path("src/smeltery/bench/static/app.css")).read_text()
    assert "prefers-reduced-motion" in css


def test_focus_is_visible():
    css = (pathlib.Path("src/smeltery/bench/static/app.css")).read_text()
    assert ":focus-visible" in css


def test_context_every_run_page_shows_campaign_and_digest(run_dir):
    from fastapi.testclient import TestClient

    from bench.fakes import FakeArtifactStore, FakeIdentity, FakeRunStore, FakeTaskQueue, FixedClock
    from smeltery.bench.app import create_app

    c = TestClient(
        create_app(
            run_store=FakeRunStore(run_dir),
            task_queue=FakeTaskQueue(),
            identity=FakeIdentity("mg"),
            clock=FixedClock(),
            artifact_store=FakeArtifactStore(run_dir / ".art"),
        )
    )
    r = c.get("/run")
    assert r.status_code == 200
    import json

    rec = json.loads(list(run_dir.glob("run-*.json"))[0].read_text())
    assert rec["campaign"] in r.text
    assert rec["input_digest"][:12] in r.text
