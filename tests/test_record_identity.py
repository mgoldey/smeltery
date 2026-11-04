import json
import subprocess
from pathlib import Path

from smeltery.record import RunRecord, smeltery_identity

REPO = Path(__file__).resolve().parents[1]


def test_an_editable_install_records_the_checked_out_commit_not_the_build_stamp():
    ident = smeltery_identity()
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()
    assert ident["commit"] == head and ident["provenance"].startswith("INFERRED")
    assert set(ident) == {"version", "source", "commit", "dirty", "provenance"}


def test_the_record_carries_it_and_it_is_not_in_the_input_digest():
    a = RunRecord("c", {"x": 1}, [], {})
    b = RunRecord("c", {"x": 1}, [], {}, smeltery={"commit": "other"})
    assert "smeltery" in json.loads(a.to_json()) and a.input_digest == b.input_digest


def test_a_record_written_before_the_field_existed_reads_back_with_no_identity():
    old = json.loads(RunRecord("c", {}, [], {}).to_json())
    del old["smeltery"]
    assert RunRecord.from_json(json.dumps(old)).smeltery is None


def test_a_record_with_the_field_round_trips_it():
    rec = RunRecord("c", {}, [], {}, smeltery={"commit": "abc"})
    assert RunRecord.from_json(rec.to_json()).smeltery == {"commit": "abc"}
