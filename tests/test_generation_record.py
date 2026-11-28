"""Generator rejections in the run record (docs/superpowers/specs/2026-11-28-generator-rejections-in-the-record.md).

Ferric-free: plans are built (RDKit poses) and stand-in numbers fill the measurement."""

from __future__ import annotations

import json

import pytest
from test_decisions import CONFIG

from smeltery import core_record
from smeltery.cli import ConfigError, build_plan, load_config, run_plan, write_outputs
from smeltery.generate.base import REASONS, GenerationResult, Rejection
from smeltery.generate.record import (
    REJECTED_SMILES_CAP,
    GenerationFileError,
    generation_block,
    load_generation,
    write_generation,
)
from smeltery.model import Candidate
from smeltery.record import RunRecord


def _result(n_kept=3, rejections=()):
    kept = [Candidate(f"c{i}", "CC") for i in range(n_kept)]
    return GenerationResult(kept, [Rejection(i, r, "", s) for i, (r, s) in enumerate(rejections)], {})


def _config(tmp_path, result=None, name="generation.json"):
    p = tmp_path / "c.toml"
    text = CONFIG
    if result is not None:
        write_generation(tmp_path / name, result, generator="fake")
        text += f'\n[generation]\nfile = "{name}"\n'
    p.write_text(text)
    return p


def _fill(pl):
    for c in pl.candidates:
        c.per_pose["dE_int"] = [float(i) + 0.1 * len(c.name) for i in range(len(c.poses))]


def _digest(plan):
    return RunRecord(plan.cfg["campaign"], plan.inputs, plan.tiers, {}).input_digest


REJ = [("duplicate", "CC"), ("size", "C"), ("duplicate", "CCC"), ("scaffold_lost", None)]


def test_the_block_counts_every_closed_reason_and_caps_the_list():
    doc = {"n_returned": 3, "rejected": [{"index": i, "reason": "size", "smiles": "C"} for i in range(80)]}
    b = generation_block(doc)
    assert list(b["rejected_by_reason"]) == list(REASONS)  # all reasons, zero included, in the closed order
    assert b["rejected_by_reason"]["size"] == 80 and b["rejected_by_reason"]["duplicate"] == 0
    assert (b["n_raw"], b["n_returned"], b["rejected_total"]) == (83, 3, 80)
    assert b["rejected_listed"] == len(b["rejected"]) == REJECTED_SMILES_CAP
    assert [r["index"] for r in b["rejected"]] == list(range(REJECTED_SMILES_CAP))


def test_a_file_with_an_unknown_reason_or_schema_is_refused(tmp_path):
    p = write_generation(tmp_path / "g.json", _result(rejections=REJ))
    doc = json.loads(p.read_text())
    doc["rejected"][0]["reason"] = "vibes"
    p.write_text(json.dumps(doc))
    with pytest.raises(GenerationFileError, match="reason"):
        load_generation(p)
    p.write_text(json.dumps({"schema": "other"}))
    with pytest.raises(GenerationFileError, match="not a"):
        load_generation(p)


def test_the_run_record_holds_the_rejections_in_the_funnel_and_the_core_record(tmp_path):
    plan = build_plan(load_config(_config(tmp_path, _result(rejections=REJ))))
    rec, _ = run_plan(plan, measure=_fill)
    path = write_outputs(plan, rec, tmp_path / "out")
    res = json.loads(path.read_text())["results"]
    first = res["funnel"][0]
    assert (first["stage"], first["kind"]) == ("generate", "generate")
    assert (first["candidates_in"], first["candidates_out"]) == (7, 3)
    assert first["rejected_by_reason"]["duplicate"] == 2 and first["rejected_by_reason"]["scaffold_lost"] == 1
    assert [(r["reason"], r["smiles"]) for r in first["rejected"]] == [(r, s) for r, s in REJ]
    assert res["generation_in"]["file"] == str((tmp_path / "generation.json").resolve())
    core = core_record.core_fields(
        json.loads(path.read_text()),
        legacy_name=path.name,
        legacy_sha256="0" * 64,
        manifest={"name": "m", "sha256": "1" * 64, "role": "manifest"},
        producer={"name": "smeltery", "version": "0"},
        dependencies=[],
    )
    assert core["ext"]["smeltery"]["generation"]["rejected_by_reason"] == first["rejected_by_reason"]
    assert "kind" not in core["ext"]["smeltery"]["generation"]
    assert any(d["sha256"] == res["generation_in"]["sha256"] and d["role"] == "input" for d in core["data"])


def test_a_run_without_a_generation_step_is_unchanged(tmp_path):
    plain = build_plan(load_config(_config(tmp_path)))
    rec, _ = run_plan(plain, measure=_fill)
    res = rec.results
    assert "generation_in" not in res and all(r["kind"] != "generate" for r in res["funnel"])
    assert "generation" not in plain.inputs
    with_gen = build_plan(load_config(_config(tmp_path, _result(rejections=REJ))))
    assert _digest(with_gen) != _digest(plain)  # the file is an input once named
    again = build_plan(load_config(_config(tmp_path)))
    assert _digest(again) == _digest(plain)


def test_a_missing_or_malformed_generation_section_is_a_config_error(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text(CONFIG + '\n[generation]\nfile = "nope.json"\n')
    with pytest.raises(ConfigError, match="does not exist"):
        build_plan(load_config(p))
    p.write_text(CONFIG + "\n[generation]\n")
    with pytest.raises(ConfigError, match="file is required"):
        load_config(p)


def test_the_dataset_carries_the_counts_and_only_for_a_run_that_has_them(tmp_path):
    from smeltery import dataset_export as D

    plan = build_plan(load_config(_config(tmp_path, _result(rejections=REJ))))
    rec, _ = run_plan(plan, measure=_fill)
    write_outputs(plan, rec, tmp_path / "out")
    rows = D.run_rows(tmp_path / "out")
    meta = D.file_metadata(rows)
    (run_id,) = json.loads(meta["smeltery.runs"])
    got = json.loads(meta["smeltery.generation"])[run_id]
    assert got["candidates_in"] == 7 and got["rejected_by_reason"]["duplicate"] == 2
    assert "rejected" not in got  # SMILES stay in the run record
    plain = [{k: v for k, v in r.items() if k != "generation"} for r in rows]
    assert "smeltery.generation" not in D.file_metadata(plain)
