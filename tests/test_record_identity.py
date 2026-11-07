import json
import subprocess
from pathlib import Path

from smeltery.record import RunRecord, smeltery_identity

REPO = Path(__file__).resolve().parents[1]


def test_an_editable_install_records_the_checked_out_commit_not_the_build_stamp():
    ident = smeltery_identity()
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()
    assert ident["commit"] == head and ident["provenance"].startswith("INFERRED")
    assert set(ident) == {"version", "source", "commit", "dirty", "provenance", "git_remote", "git_branch"}


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


def test_unknown_top_level_keys_and_a_missing_schema_version_still_load():
    d = json.loads(RunRecord("c", {"x": 1}, [], {}).to_json())
    del d["schema_version"]
    d["from_the_future"] = {"a": 1}
    rec = RunRecord.from_json(json.dumps(d))
    assert rec.campaign == "c" and rec.input_digest == d["input_digest"]
    assert json.loads(rec.to_json())["schema_version"] == 1


def test_remote_credentials_are_stripped():
    from smeltery.record import strip_credentials

    assert strip_credentials("https://user:tok@github.com/o/r.git") == "https://github.com/o/r.git"
    assert strip_credentials("git@github.com:o/r.git") == "git@github.com:o/r.git"
    assert strip_credentials("ssh://git@host/o/r.git") == "ssh://git@host/o/r.git"
