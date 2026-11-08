"""The report states its floor, lists what the result cannot tell you, and the snapshot carries judgement."""

import json
import math

from gui_fixture import make_run_dir

from smeltery.gui import report, results
from smeltery.gui.limits import floor_statement, load_limits, parse_limits


def test_floor_sentence_states_value_unit_source_and_what_it_forbids():
    s = floor_statement(4.07, "kcal/mol", "config [cut] floor")
    assert "4.07 kcal/mol" in s and "(config [cut] floor)" in s
    assert "Differences smaller than 4.07 kcal/mol are reported as ties, not rankings" in s


def test_unmeasured_or_zero_floor_says_so_and_claims_no_threshold():
    for bad in (None, math.nan):
        s = floor_statement(bad, "kcal/mol", "x")
        assert "unmeasured" in s and "reported as ties" not in s
    assert "0 kcal/mol" in floor_statement(0.0, "kcal/mol", "x") and "no systematic bound" in floor_statement(
        0.0, "kcal/mol", ""
    )


def test_limits_parse_from_the_real_doc():
    data = load_limits()
    assert data is not None
    ids = [r["id"] for r in data["measured"]]
    assert ids[:2] == ["L1", "L2"] and len(ids) >= 16
    l2 = next(r for r in data["measured"] if r["id"] == "L2")
    assert l2["magnitude"].startswith("4.07") and "Ranking analogues" in l2["forbids"]
    assert [r["id"] for r in data["unmeasured"]][:2] == ["U1", "U2"]
    assert all(r["status"] for r in data["unmeasured"])


def test_limits_missing_or_empty_doc_is_none_not_an_error(tmp_path):
    assert load_limits(tmp_path / "nope.md") is None
    empty = tmp_path / "e.md"
    empty.write_text("# nothing here\n")
    assert load_limits(empty) is None
    assert parse_limits("# nothing")["measured"] == []


def test_cli_report_has_floor_and_limits_but_no_judgement_sections(tmp_path):
    out = make_run_dir(tmp_path / "run")
    html = report.render_html(results.load_run(out))
    assert "The floor is 0 kcal/mol" in html and "What this result cannot tell you" in html and "L16" in html
    assert "<h2>Comments</h2>" not in html and "<h2>Decisions</h2>" not in html


def test_report_omits_limits_when_docs_are_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "load_limits", lambda: None)
    html = report.render_html(results.load_run(make_run_dir(tmp_path / "run")))
    assert "cannot tell you" not in html


def test_report_states_a_measured_floor_from_the_record(tmp_path):
    out = make_run_dir(tmp_path / "run")
    (p,) = out.glob("run-*.json")
    rec = json.loads(p.read_text())
    rec["inputs"]["floor"] = {"value": 4.07, "source": "DDE_NOISE_FLOOR_KCAL_MOL"}
    p.write_text(json.dumps(rec))
    html = report.render_html(results.load_run(out))
    assert "The floor is 4.07 kcal/mol (DDE_NOISE_FLOOR_KCAL_MOL). Differences smaller than 4.07 kcal/mol" in html
