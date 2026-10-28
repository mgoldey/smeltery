"""The HTML report: one file a chemist opens without Python. It shows every candidate and the floor's source, an
UNRANKED banner only for an unranked cut, no order inside a tie group, pinned CDN files, and no way for a record
string to break out of the page; and it renders in a real browser with the CDN unreachable, saying the viewer could
not load instead of leaving a blank box.

Until `smeltery.gui.results` is merged, the data layer is the contract-shaped stand-in in tests/_report_shims.
"""

import importlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from html import escape
from pathlib import Path

import pytest
from gui_fixture import make_run_dir

from smeltery.gui import report

EVIL = "</script><img src=x onerror=alert(1)>"
FLOOR_SOURCE = "config [cut] floor (explicit; the tier's own is unmeasured)"
JSON_BLOCK = re.compile(r'<script type="application/json" id="([^"]+)">(.*?)</script>', re.S)


@pytest.fixture
def results(monkeypatch):
    try:
        return importlib.import_module("smeltery.gui.results")
    except ModuleNotFoundError as e:
        if e.name != "smeltery.gui.results":
            raise
    shim = Path(__file__).parent / "_report_shims" / "results.py"
    spec = importlib.util.spec_from_file_location("smeltery.gui.results", shim)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "smeltery.gui.results", mod)
    spec.loader.exec_module(mod)
    return mod


def _edit_record(out: Path, edit) -> None:
    (p,) = out.glob("run-*.json")
    rec = json.loads(p.read_text())
    edit(rec)
    p.write_text(json.dumps(rec))


def _render(results, out: Path, dest: Path) -> str:
    report.write_report(results.load_run(out), dest)
    return dest.read_text(encoding="utf-8")


@pytest.fixture
def ranked(results, tmp_path):
    return _render(results, make_run_dir(tmp_path), tmp_path / "ranked.html")


@pytest.fixture
def unranked(results, tmp_path):
    return _render(results, make_run_dir(tmp_path, unranked=True), tmp_path / "unranked.html")


@pytest.fixture
def evil_out(results, tmp_path):
    """The fixture with 4-Cl renamed to a name that tries to close the script element and run code."""
    out = make_run_dir(tmp_path)
    (p,) = out.glob("run-*.json")
    p.write_text(p.read_text().replace('"4-Cl"', json.dumps(EVIL)))
    return out


def test_ranked_report_shows_every_candidate_floor_source_and_survivor(ranked):
    for name in ("4-F", "4-Cl", "4-Br"):
        assert f"<td>{name}</td>" in ranked
    assert "<dd>benzoic  (OC(=O)c1ccccc1)</dd>" in ranked
    assert escape(FLOOR_SOURCE) in ranked
    assert "Survivors (keep = 1):</strong> 4-F" in ranked
    assert "UNRANKED" not in ranked
    assert 'class="keep"' in ranked  # the cut placed a boundary, so the line is drawn


def test_unranked_report_is_a_banner_naming_the_tie_group_with_no_keep_line(unranked):
    assert '<div class="verdict unranked" role="alert"><strong>UNRANKED</strong>' in unranked
    assert "keep = 2 falls inside tie group 2 (4-Br, 4-Cl)" in unranked
    assert 'class="keep"' not in unranked
    assert "Survivors" not in unranked
    assert "✓ kept" not in unranked


def test_tie_group_members_are_sorted_by_name_and_say_they_have_no_order(results, tmp_path):
    out = make_run_dir(tmp_path)
    # Put the tie group in reverse name order in the record: the page must not carry that order through.
    _edit_record(out, lambda r: r["results"]["cut"]["groups"][1].sort(reverse=True))
    html = _render(results, out, tmp_path / "r.html")
    table = html[html.index('<table class="cands">') :]
    assert table.index(">4-Br<") < table.index(">4-Cl<")
    assert "group 2 · no order within group" in html


def test_cdn_files_are_pinned_exactly_with_integrity(ranked):
    pins = {
        "https://cdn.jsdelivr.net/npm/@rdkit/rdkit@2026.9.1/dist/RDKit_minimal.js",
        "https://cdn.jsdelivr.net/npm/@rdkit/rdkit@2026.9.1/dist/RDKit_minimal.wasm",
        "https://cdn.jsdelivr.net/npm/molstar@5.13.1/build/viewer/molstar.js",
        "https://cdn.jsdelivr.net/npm/molstar@5.13.1/build/viewer/molstar.css",
    }
    assert set(re.findall(r'https://[^"\s]+', ranked)) == pins
    assert set(report.SRI) == pins - {report.RDKIT_WASM}
    assert all(h.startswith("sha384-") for h in report.SRI.values())


def test_inlined_json_cannot_close_its_script_element(ranked):
    blocks = dict(JSON_BLOCK.findall(ranked))
    assert set(blocks) == {"smeltery-data", "smeltery-cdn"}
    for text in blocks.values():
        assert "<" not in text and ">" not in text
        json.loads(text)
    data = json.loads(blocks["smeltery-data"])
    assert [p["name"] for p in data["poses"]][0] == "benzoic"
    assert all(p["sdf"].count("$$$$") == p["n"] for p in data["poses"])


def test_malicious_candidate_name_is_escaped_everywhere(results, evil_out, tmp_path):
    html = _render(results, evil_out, tmp_path / "evil.html")
    assert EVIL not in html
    assert "<img src=x" not in html
    assert escape(EVIL) in html  # shown, as text
    assert html.count("<script") == 3  # the two JSON blocks and the page script; nothing injected
    blocks = dict(JSON_BLOCK.findall(html))
    assert EVIL in [m[0] for m in json.loads(blocks["smeltery-data"])["mols"]]


def test_positive_floor_draws_a_band_and_names_it(results, tmp_path):
    out = make_run_dir(tmp_path)
    _edit_record(out, lambda r: r["inputs"]["floor"].update(value=0.5, source="charge-model sensitivity, measured"))
    html = _render(results, out, tmp_path / "f.html")
    assert html.count('class="floor"') == 3
    assert "±floor (0.5 kcal/mol)" in html
    assert "charge-model sensitivity, measured" in html


def test_missing_pose_files_still_render_everything_else(results, tmp_path):
    out = make_run_dir(tmp_path)
    _edit_record(out, lambda r: r["results"].pop("poses"))
    html = _render(results, out, tmp_path / "np.html")
    assert "Pose files were not recorded in this run" in html
    assert 'id="molstar"' not in html
    assert html.count('<g class="row">') == 3 and '<table class="matrix">' in html


def test_main_writes_the_report_and_refuses_an_ambiguous_out_dir(results, tmp_path, capsys):
    out = make_run_dir(tmp_path)
    dest = tmp_path / "sub" / "r.html"
    assert report.main([str(out), "-o", str(dest)]) == 0
    assert dest.read_text().startswith("<!doctype html>")
    (p,) = out.glob("run-*.json")
    shutil.copy(p, out / "run-000000000000.json")
    assert report.main([str(out), "-o", str(dest)]) == 2
    assert "error:" in capsys.readouterr().err


# ------------------------------------------------------------------------------------------- real browser render

RENDER_JS = r"""
const { chromium } = require('playwright');
const fs = require('fs'), path = require('path');
(async () => {
  const [page_, shot, mode, mirror, width] = process.argv.slice(2);
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: +width, height: 900 } });
  const errors = []; let alerted = false;
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('dialog', (d) => { alerted = true; d.dismiss(); });
  await page.route(/^https?:/, (route) => {
    const m = route.request().url().match(/^https:\/\/cdn\.jsdelivr\.net\/npm\/(.*)$/);
    const f = mode === 'mirror' && m ? path.join(mirror, m[1]) : null;
    if (f && fs.existsSync(f)) {
      const ct = f.endsWith('.js') ? 'text/javascript' : f.endsWith('.css') ? 'text/css' : 'application/wasm';
      return route.fulfill({ status: 200, body: fs.readFileSync(f),
        headers: { 'content-type': ct, 'access-control-allow-origin': '*' } });
    }
    return route.abort();
  });
  await page.goto('file://' + path.resolve(page_));
  await page.waitForFunction(() => {
    const cells = [...document.querySelectorAll('.mol2d')];
    const msg = document.getElementById('viewer-msg');
    return cells.every((c) => c.querySelector('img') || !/loading|needs RDKit/.test(c.textContent)) &&
      (!document.getElementById('molstar') || !/^Loading Mol|needs JavaScript/.test(msg.textContent));
  }, null, { timeout: 60000 });
  await page.waitForTimeout(mode === 'mirror' ? 2500 : 200);
  const info = await page.evaluate(() => ({
    viewerMsg: (document.getElementById('viewer-msg') || {}).textContent || '',
    viewerFailed: !!document.querySelector('#molstar.failed'),
    canvases: document.querySelectorAll('#molstar canvas').length,
    mol2dImgs: document.querySelectorAll('.mol2d img').length,
    mol2dText: [...document.querySelectorAll('.mol2d')].map((c) => c.textContent),
    forestRows: document.querySelectorAll('svg.forest g.row').length,
    forestWidth: document.querySelector('svg.forest').getBoundingClientRect().width,
    tableRows: document.querySelectorAll('table.cands tbody tr').length,
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    injected: !!document.querySelector('img[src="x"]'),
  }));
  info.errors = errors; info.alerted = alerted;
  if (shot) await page.screenshot({ path: shot, fullPage: true });
  console.log(JSON.stringify(info));
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


def _playwright_ok() -> bool:
    node = shutil.which("node")
    if not node:
        return False
    return subprocess.run([node, "-e", "require('playwright')"], capture_output=True).returncode == 0


needs_browser = pytest.mark.skipif(not _playwright_ok(), reason="needs node with the playwright package")


def _browse(tmp_path: Path, page: Path, mode="block", mirror="", width=1100, shot="") -> dict:
    js = tmp_path / "render.js"
    js.write_text(RENDER_JS)
    r = subprocess.run(
        ["node", str(js), str(page), shot, mode, mirror, str(width)], capture_output=True, text=True, timeout=180
    )
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


@needs_browser
@pytest.mark.parametrize("width", [1100, 390])
def test_browser_without_network_renders_static_parts_and_says_the_viewer_could_not_load(results, tmp_path, width):
    page = tmp_path / "ranked.html"
    report.write_report(results.load_run(make_run_dir(tmp_path)), page)
    info = _browse(tmp_path, page, width=width)
    assert info["errors"] == []
    assert info["forestRows"] == 3 and info["tableRows"] == 3 and info["forestWidth"] > 300
    assert info["overflow"] == 0  # no horizontal page scroll, even at phone width
    assert info["viewerFailed"] and "The 3D viewer could not load (Mol* 5.13.1" in info["viewerMsg"]
    assert all(t.startswith("2D drawing unavailable") for t in info["mol2dText"])


@needs_browser
def test_browser_does_not_run_a_malicious_name(results, evil_out, tmp_path):
    page = tmp_path / "evil.html"
    report.write_report(results.load_run(evil_out), page)
    info = _browse(tmp_path, page)
    assert not info["injected"] and not info["alerted"] and info["errors"] == []
    assert info["tableRows"] == 3


@needs_browser
def test_browser_with_cdn_files_draws_structures_and_poses(results, tmp_path):
    """With a local copy of the pinned npm files (SMELTERY_REPORT_CDN_MIRROR, laid out as `<pkg>@<version>/...`,
    i.e. the URL path after /npm/), RDKit.js and Mol* load under the page's integrity hashes and draw."""
    import os

    mirror = os.environ.get("SMELTERY_REPORT_CDN_MIRROR")
    if not mirror:
        pytest.skip("set SMELTERY_REPORT_CDN_MIRROR to a local copy of the pinned npm files")
    page = tmp_path / "ranked.html"
    report.write_report(results.load_run(make_run_dir(tmp_path)), page)
    info = _browse(tmp_path, page, mode="mirror", mirror=mirror)
    assert info["errors"] == [] and not info["viewerFailed"]
    assert info["mol2dImgs"] == 3 and info["canvases"] >= 1
    assert info["viewerMsg"].startswith("4-F, pose 0 with the parent")
