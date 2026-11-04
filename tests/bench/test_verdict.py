import json
from pathlib import Path

import pytest
from gui_fixture import make_run_dir

from smeltery.bench.verdict import verdict_of


def record_path(run_dir):
    return next(Path(run_dir).glob("run-*.json"))


@pytest.fixture
def run_dir(tmp_path):
    return make_run_dir(tmp_path)


@pytest.fixture
def unranked_run_dir(tmp_path):
    return make_run_dir(tmp_path, unranked=True)


def test_a_cut_with_survivors_is_RESOLVED(run_dir):
    v = verdict_of(json.loads(record_path(run_dir).read_text()))
    assert v.state == "RESOLVED" and v.winner == "4-F"


def test_an_unranked_cut_is_UNRANKED(unranked_run_dir):
    v = verdict_of(json.loads(record_path(unranked_run_dir).read_text()))
    assert v.state == "UNRANKED" and v.winner is None and v.floor is not None


def test_a_record_without_a_cut_is_INCOMPLETE(run_dir):
    rec = json.loads(record_path(run_dir).read_text())
    del rec["results"]["cut"]
    v = verdict_of(rec)
    assert v.state == "INCOMPLETE" and "cut" in v.missing


def test_INCOMPLETE_never_presents_as_a_winner(run_dir):
    rec = json.loads(record_path(run_dir).read_text())
    del rec["results"]["cut"]
    assert verdict_of(rec).winner is None


def test_an_empty_results_block_is_INCOMPLETE_not_a_crash():
    assert verdict_of({"results": {}}).state == "INCOMPLETE"
