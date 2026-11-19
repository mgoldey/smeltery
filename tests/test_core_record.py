"""The core run record (`attestation.run/1`) written beside smeltery's own record (smeltery.core_record).

Needs the `attestation` extra (attestation-schema); skipped without it, except the notice test. CI's 3.11 `test` job
installs the extra, so these run there too.
Regenerate the golden file after a deliberate change with:

    SMELTERY_REGEN_CORE_GOLDEN=1 .venv/bin/python -m pytest tests/test_core_record.py -q -k golden
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from smeltery import core_record
from smeltery.core_record import dependency, provenance_level

ROOT = Path(__file__).resolve().parents[1]
PREBUILT = ROOT / "src" / "smeltery" / "prebuilt"
GOLDEN = ROOT / "tests" / "data" / "core_record" / "benzoic-rhf.run.json"
#: The converter's producer version is the installed smeltery's, which changes every commit; the golden pins the rest.
GOLDEN_PRODUCER = {"name": "smeltery record-core", "version": None}

needs_schema = pytest.mark.skipif(not core_record.AVAILABLE, reason=core_record.NOT_INSTALLED)


def _copy_run(tmp_path: Path, name: str) -> Path:
    dst = tmp_path / name
    shutil.copytree(PREBUILT / name / "run", dst, ignore=shutil.ignore_patterns("temporal"))
    return dst


def _legacy(run_dir: Path) -> Path:
    (path,) = run_dir.glob("run-*.json")
    return path


# ---- the mapping, no schema package needed ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ident", "want"),
    [
        (  # a stamped build: VERIFIED, the stamp's sha is the build identity
            {
                "version": "0.1.0rc12",
                "source": "ferric.__build__",
                "commit": "8e408dd82698b1c344272960bb62778d9a5d4d9a",
                "dirty": False,
                "provenance": "VERIFIED from ferric.__build__ (stamped at build time)",
            },
            ("verified", "8e408dd82698b1c344272960bb62778d9a5d4d9a", "0.1.0rc12", False, None),
        ),
        (  # an index release: VERIFIED by its immutable version, no sha
            {
                "version": "0.1.0rc10",
                "source": "package index",
                "commit": None,
                "dirty": False,
                "provenance": "VERIFIED: index release 0.1.0rc10 (immutable)",
            },
            ("verified", None, "0.1.0rc10", False, "index"),
        ),
        (  # a local checkout: INFERRED, its HEAD now
            {
                "version": "0.1.0",
                "source": "file:///x/ferric",
                "commit": "a" * 40,
                "dirty": True,
                "provenance": "INFERRED from the local source directory's current git state",
            },
            ("inferred", "a" * 40, "0.1.0", True, "file"),
        ),
        (  # an unstamped wheel: INFERRED, the pip hash only in the evidence
            {
                "version": "0.1.0",
                "source": "file:///x/ferric-0.1.0-cp312-linux.whl",
                "commit": None,
                "dirty": False,
                "provenance": "INFERRED from the installed wheel ferric-0.1.0-cp312-linux.whl (sha256=ab)",
            },
            ("inferred", None, "0.1.0", False, "file"),
        ),
        (  # nothing found
            {"version": None, "source": None, "commit": None, "dirty": None, "provenance": "UNKNOWN"},
            ("unknown", None, None, None, None),
        ),
        (  # a git install: VERIFIED with the commit, the URL as the source (credentials stripped)
            {
                "version": "0.1.0",
                "source": "https://user:tok@github.com/x/ferric.git",
                "commit": "b" * 40,
                "dirty": False,
                "provenance": "VERIFIED from direct_url.json (git install)",
            },
            ("verified", "b" * 40, "0.1.0", False, "git+https://github.com/x/ferric.git"),
        ),
        (  # conda / a distro: INFERRED, the installer as the source
            {
                "version": "2025.03.1",
                "source": "installed by conda",
                "commit": None,
                "dirty": None,
                "provenance": "INFERRED: version 2025.03.1 as installed by conda (not an index release)",
            },
            ("inferred", None, "2025.03.1", None, "conda"),
        ),
        (  # a distro package manager: the OS provided it
            {
                "version": "2024.1",
                "source": "installed by debian",
                "commit": None,
                "dirty": None,
                "provenance": "INFERRED: version 2024.1 as installed by debian (not an index release)",
            },
            ("inferred", None, "2024.1", None, "system"),
        ),
        (  # a wheel fetched from a URL: the closed vocabulary has no kind for it, so the source is not known
            {
                "version": "0.1.0",
                "source": "https://example.org/w/ferric-0.1.0-cp312-linux.whl",
                "commit": None,
                "dirty": False,
                "provenance": "INFERRED from the installed wheel ferric-0.1.0-cp312-linux.whl (sha256=ab)",
            },
            ("inferred", None, "0.1.0", False, None),
        ),
    ],
)
def test_every_rung_of_the_identity_ladder_maps_to_the_core_enum_the_same_way(ident, want):
    dep = dependency("anything", ident)  # the name is not consulted: ferric gets no special case
    assert (dep["provenance"], dep["build_sha"], dep["version"], dep["dirty"], dep["source"]) == want
    assert set(dep) == {"name", "version", "build_sha", "dirty", "provenance", "evidence", "source"}
    assert dep["evidence"] == (None if want[0] == "unknown" else ident["provenance"])


def test_no_identity_at_all_is_unknown_with_every_field_null():
    assert dependency("ferric", None) == {
        "name": "ferric",
        "version": None,
        "build_sha": None,
        "dirty": None,
        "provenance": "unknown",
        "evidence": None,
        "source": None,
    }
    assert provenance_level("VERIFIED: x") == "verified" and provenance_level(None) == "unknown"


def test_a_docking_seed_list_is_recorded_as_a_list_and_a_malformed_one_makes_seeds_unknown():
    assert core_record.seeds([{"name": "paired_poses", "seed": 1}]) == {"paired_poses": 1}
    both = [{"name": "docking", "seeds": [7]}, {"name": "paired_poses", "seed": 1}]
    assert core_record.seeds(both) == {"docking": [7], "paired_poses": 1}
    assert core_record.seeds([{"name": "field_interaction"}]) is None
    # never partial: a list the schema cannot hold, or one name with two seeds, is unknown rather than a guess
    assert core_record.seeds([{"name": "docking", "seeds": []}, {"name": "paired_poses", "seed": 1}]) is None
    assert core_record.seeds([{"name": "x", "seed": 1}, {"name": "x", "seed": 2}]) is None


def test_a_run_written_by_two_ferric_builds_records_ferric_unknown_naming_the_mix():
    stamped = {"version": "0.1.0", "source": "ferric.__build__", "commit": "a" * 40, "dirty": False,
               "provenance": "VERIFIED from ferric.__build__ (stamped at build time)"}  # fmt: skip
    one = {"identity": "recorded", "ferric": {"git_sha": "a" * 40, "dirty": False}, "n_rows": 8}
    legacy = {"ferric": stamped, "host": {"python": "3.11.15"}, "results": {"builds": [one]}}
    assert core_record.recorded_dependencies(legacy)[0]["provenance"] == "verified"  # one build: not a mix
    two = {"identity": "recorded", "ferric": {"git_sha": "b" * 40, "dirty": True}, "n_rows": 4}
    legacy["results"]["builds"] = [one, two, {"identity": "unrecorded", "n_rows": 2}]
    for deps in (core_record.recorded_dependencies(legacy), core_record.live_dependencies(legacy)):
        ferric = deps[0]
        assert (ferric["name"], ferric["provenance"], ferric["version"], ferric["build_sha"]) == (
            "ferric",
            "unknown",
            None,
            None,
        )
        assert "2 ferric builds" in ferric["evidence"] and "bbbbbbbbbbbb (dirty): 4 row(s)" in ferric["evidence"]


def test_a_run_written_by_two_smeltery_commits_records_code_unknown_naming_each_commit_and_its_rows():
    ident = {"commit": "c" * 40, "dirty": True, "version": "0.0.2", "git_remote": None,
             "provenance": "INFERRED from the local source directory's current git state"}  # fmt: skip
    one = {"identity": "recorded", "smeltery": {"commit": "a" * 40, "dirty": True}, "n_rows": 1}
    assert core_record._code(ident, None, [one])["git_sha"] == "c" * 40  # one commit: not a mix
    two = {"identity": "recorded", "smeltery": {"commit": "b" * 40, "dirty": False}, "n_rows": 111}
    code = core_record._code(ident, None, [one, two, {"identity": "unrecorded", "n_rows": 46}])
    assert code == {
        "git_sha": None,
        "dirty": None,
        "repo": None,
        "version": None,  # a build row records a commit, not a version: no shared version is known
        "provenance": "unknown",
        "evidence": f"mixed: {'a' * 40} (1 row), {'b' * 40} (111 rows)",
    }


def test_code_is_on_the_dependency_ladder_and_keeps_the_version_that_ran():
    ident = {
        "commit": "c" * 40,
        "dirty": True,
        "provenance": "INFERRED from the local source directory's current git state",
        "source": "file:///somewhere/smeltery",
        "version": "0.0.2.dev383",
        "git_remote": "https://github.com/x/smeltery",
    }
    assert core_record._code(ident, "ignored") == {
        "git_sha": "c" * 40,
        "dirty": True,
        "repo": "https://github.com/x/smeltery",
        "version": "0.0.2.dev383",
        "provenance": "inferred",
        "evidence": ident["provenance"],
    }
    # a record from before the identity block: the version the run reported, provenance unknown
    old = core_record._code(None, "0.0.1")
    assert (old["version"], old["provenance"], old["git_sha"], old["evidence"]) == ("0.0.1", "unknown", None, None)


@needs_schema  # the wire spelling of NaN is the schema's (`attestation_schema.spell_value`)
def test_metrics_label_what_they_are_about_and_write_undefined_values_as_nan():
    results = {
        "funnel": [
            {"stage": "structure", "candidates_in": None, "candidates_out": None},  # counts nothing: not reported
            {"stage": "poses", "candidates_in": 3, "candidates_out": 3},
            {"stage": "cut", "candidates_in": 2, "candidates_out": None, "unranked": True},  # ran, no survivor set
        ],
        "cut": {"survivors": None, "unranked": True},
        "units": {"dE_int": "kcal/mol"},
        "candidate_order": ["p", "b", "a"],
        "paired_ddE": {"a": {"mean": 1.0, "sem": float("nan"), "n": 1}, "b": {"mean": -1.0, "sem": 0.1, "n": 3}},
    }
    got = [(m["name"], m["labels"], m["value"], m["direction"], m["unit"]) for m in core_record.metrics(results)]
    assert got == [
        ("stage.candidates_in", {"stage": "poses"}, 3, None, None),
        ("stage.survivors", {"stage": "poses"}, 3, None, None),
        ("stage.candidates_in", {"stage": "cut"}, 2, None, None),
        ("stage.survivors", {"stage": "cut"}, "NaN", None, None),
        ("cut.survivors", {}, "NaN", None, None),
        ("paired_ddE.mean", {"analogue": "b"}, -1.0, "min", "kcal/mol"),  # config order, not dict order
        ("paired_ddE.sem", {"analogue": "b"}, 0.1, None, "kcal/mol"),
        ("paired_ddE.n", {"analogue": "b"}, 3, None, None),
        ("paired_ddE.mean", {"analogue": "a"}, 1.0, "min", "kcal/mol"),
        ("paired_ddE.sem", {"analogue": "a"}, "NaN", None, "kcal/mol"),  # one pose: computed, undefined
        ("paired_ddE.n", {"analogue": "a"}, 1, None, None),
    ]
    # no unit is invented: a record that does not say what dE_int is in gives null
    del results["units"]
    assert {m["unit"] for m in core_record.metrics(results)} == {None}


# ---- real prebuilt runs through attestation-schema's writer ----------------------------------------------------


@needs_schema
def test_golden_one_prebuilt_run_gives_the_committed_core_record_byte_for_byte(tmp_path):
    run_dir = _copy_run(tmp_path, "benzoic-rhf")
    path = core_record.write_core(_legacy(run_dir), producer_info=GOLDEN_PRODUCER)
    got = path.read_bytes()
    if os.environ.get("SMELTERY_REGEN_CORE_GOLDEN"):
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_bytes(got)
    assert got == GOLDEN.read_bytes()


@needs_schema
@pytest.mark.parametrize("name", sorted(p.name for p in PREBUILT.iterdir() if (p / "run").is_dir()))
def test_every_prebuilt_run_converts_and_reads_back_canonically(tmp_path, name):
    import attestation_schema as a
    from attestation_schema.layout import build_manifest

    run_dir = _copy_run(tmp_path, name)
    legacy_path = _legacy(run_dir)
    legacy = json.loads(legacy_path.read_text())
    path = core_record.write_core(legacy_path)
    assert path == run_dir / "attestation" / "run.json"
    rec = a.read(run_dir)
    assert a.dumps(rec) == path.read_bytes()  # canonical: the file IS the record's bytes
    assert rec.run_id == legacy_path.stem.removeprefix("run-") and rec.family == legacy["campaign"]
    assert rec.status == "complete" and rec.arm is None
    assert (rec.started_at, rec.finished_at) == (None, None)  # a conversion knows neither instant
    assert rec.ext["smeltery"]["input_digest"] == legacy["input_digest"]
    assert rec.history is None  # smeltery logs no curves, so there is no sidecar
    assert not (run_dir / "attestation" / "history.jsonl").exists()
    assert rec.dependencies[0].name == "ferric"
    assert {d.source for d in rec.dependencies if d.name == "python"} == {"system"}
    commits = {b["smeltery"]["commit"] for b in legacy["results"].get("builds", []) if "smeltery" in b}
    if len(commits) > 1:  # danuglipron-halogen: rows from two smeltery commits, so no one commit ran it
        assert (rec.code.provenance, rec.code.git_sha, rec.code.dirty) == ("unknown", None, None)
        assert rec.code.evidence.startswith("mixed: ") and all(c in rec.code.evidence for c in commits)
    else:
        assert rec.code.git_sha == legacy["smeltery"]["commit"]
    ferrics = {json.dumps(b["ferric"]) for b in legacy["results"].get("builds", []) if "ferric" in b}
    if len(ferrics) > 1:  # danuglipron-halogen: SCFs from two ferric builds, so no one ferric ran it
        assert (rec.dependencies[0].provenance, rec.dependencies[0].build_sha) == ("unknown", None)
    else:
        assert rec.dependencies[0].build_sha == legacy["ferric"]["commit"]
    # the manifest binds every file the run left, the legacy record and the step files included
    manifest = json.loads((run_dir / "attestation" / "digests.json").read_bytes())
    assert manifest == build_manifest(run_dir)
    assert legacy_path.name in manifest["files"] and "poses/" in "".join(manifest["files"])
    roles = {d.name: d.role for d in rec.data}
    assert roles[legacy_path.name] == "output" and roles["attestation/digests.json"] == "manifest"
    steps = sorted(p.name for p in (run_dir / "steps").iterdir())
    assert [s.split("-", 1)[1] for s in steps] == [r["stage"] for r in legacy["results"]["funnel"]]


@needs_schema
def test_no_chemistry_in_the_core_fields_only_in_config_and_ext(tmp_path):
    run_dir = _copy_run(tmp_path, "docking")
    legacy = json.loads(_legacy(run_dir).read_text())
    core_record.write_core(_legacy(run_dir))
    rec = json.loads((run_dir / "attestation" / "run.json").read_bytes())
    core = json.dumps({k: v for k, v in rec.items() if k not in ("config", "ext")})
    chemistry = [*legacy["inputs"]["candidates"].values(), "sto-3g", "vina", "MMFF"]
    assert not [c for c in chemistry if c in core]
    assert rec["ext"]["smeltery"]["tiers"] == legacy["tiers"]
    # a unit is not chemistry: it is the one chemistry-adjacent string the core carries, and only as `unit`
    assert {m["unit"] for m in rec["metrics"]} == {None, legacy["results"]["units"]["dE_int"]}
    # docking runs Vina once per seed: the list is recorded as such, beside the paired-pose seed
    assert rec["seeds"] == {"docking": [7], "paired_poses": 20261005}
    assert rec["ext"]["smeltery"]["cut_unranked"] is True
    survivors = [m for m in rec["metrics"] if m["name"] == "cut.survivors"]
    assert [m["value"] for m in survivors] == ["NaN"]  # on the wire: the cut ran and has no survivor set


@needs_schema
def test_an_existing_core_record_is_refused_unless_forced_and_a_partial_is_never_a_run(tmp_path):
    run_dir = _copy_run(tmp_path, "benzoic-pbe")
    legacy_path = _legacy(run_dir)
    core_record.write_core(legacy_path)
    with pytest.raises(FileExistsError):
        core_record.write_core(legacy_path)
    core_record.write_core(legacy_path, overwrite=True)
    assert core_record.main(["core", str(run_dir)]) == 1  # refused, says so
    assert core_record.main(["core", str(run_dir), "--force"]) == 0

    partial = tmp_path / "p" / "partial-x.json"
    partial.parent.mkdir()
    d = json.loads(legacy_path.read_text())
    del d["results"]["cut"]
    partial.write_text(json.dumps(d))
    with pytest.raises(ValueError, match="partial"):
        core_record.write_core(partial)
    assert not (partial.parent / "attestation").exists() and not (partial.parent / "steps").exists()


@needs_schema
def test_a_live_run_writes_the_core_record_last_with_its_own_times_and_environment(tmp_path, monkeypatch, capsys):
    import attestation_schema as a
    from test_run_outputs import CONFIG, _PlanLike, fill

    from smeltery.cli import main
    from smeltery.tiers import FieldInteraction

    monkeypatch.setattr(FieldInteraction, "run", lambda self, cands, ctx: fill(_PlanLike(cands)))
    p = tmp_path / "c.toml"
    p.write_text(CONFIG)
    out_dir = tmp_path / "out"
    assert main(["run", str(p), "--out", str(out_dir)]) == 0
    printed = capsys.readouterr().out
    rec = a.read(out_dir)
    assert f"core record: {out_dir / 'attestation' / 'run.json'}" in printed
    assert rec.started_at and rec.finished_at
    # compared as text: the one spelling always has six fraction digits, so text order is time order
    assert rec.started_at <= rec.finished_at
    assert all(len(t) == len("2026-11-19T10:00:37.500000Z") for t in (rec.started_at, rec.finished_at))
    assert rec.history is None
    assert rec.producer.name == "smeltery"
    names = [d.name for d in rec.dependencies]
    assert names[:4] == ["ferric", "python", "rdkit", "numpy"]
    # the GUI contract's files are where they were, and the manifest covers them
    manifest = json.loads((out_dir / "attestation" / "digests.json").read_bytes())["files"]
    assert {"config.toml", "poses/benzoic.sdf", _legacy(out_dir).name} <= set(manifest)
    # --force reruns into the same OUT and replaces the core record rather than failing
    assert main(["run", str(p), "--out", str(out_dir), "--force"]) == 0
    assert a.read(out_dir).run_id == rec.run_id


def test_without_the_extra_a_run_prints_one_notice_and_still_succeeds(tmp_path, monkeypatch, capsys):
    from test_run_outputs import CONFIG, _PlanLike, fill

    from smeltery.cli import main
    from smeltery.tiers import FieldInteraction

    monkeypatch.setattr(core_record, "AVAILABLE", False)
    monkeypatch.setattr(FieldInteraction, "run", lambda self, cands, ctx: fill(_PlanLike(cands)))
    p = tmp_path / "c.toml"
    p.write_text(CONFIG)
    assert main(["run", str(p), "--out", str(tmp_path / "out")]) == 0
    err = capsys.readouterr().err
    assert err.count(core_record.NOT_INSTALLED) == 1
    assert not (tmp_path / "out" / "attestation").exists()
    assert core_record.main(["core", str(tmp_path / "out")]) == 2
