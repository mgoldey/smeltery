"""`smeltery export dataset` and dataset versions: smeltery as a producer of `attestation.dataset/1` (DRAFT).

The source runs are the committed `src/smeltery/prebuilt/*/run/` runs, copied and given their core records with
`smeltery record core`, so every digest here is of real bytes. The chain checked end to end is the one the schema's
lineage resolver exists for: ferric build -> smeltery run (core record) -> dataset directory -> a training run whose
core record names the dataset by content digest and the split file by its digest.

Needs attestation-schema with `attestation_schema.dataset` (>= `dataset_record.REQUIRED_SCHEMA`) and pyarrow. Without
the former these tests SKIP, naming the release -- until pyproject's `attestation` extra requires that release, from
which point `test_the_dataset_tests_cannot_skip_once_the_extra_requires_the_release` fails instead of letting CI's
test job (which installs the extra) skip them.
"""

import hashlib
import json
import re
import shutil
import tomllib
from pathlib import Path

import pytest

from smeltery import cli
from smeltery import dataset_export as D
from smeltery import dataset_record as DR
from smeltery import dataset_splits as S

ROOT = Path(__file__).resolve().parents[1]
PREBUILT = ROOT / "src" / "smeltery" / "prebuilt"
#: The four prebuilt runs whose legacy ferric identity and core ferric dependency agree; `MIXED` is the fifth.
CONSISTENT = ("benzoic-rhf", "benzoic-pbe", "docking", "structure")
MIXED = "danuglipron-halogen"
TWO = {"train": 0.5, "test": 0.5}


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", text)[:3])


def test_the_dataset_tests_cannot_skip_once_the_extra_requires_the_release():
    """CI's test job installs the `attestation` extra. While its floor predates attestation.dataset/1 these tests skip
    there; the commit that raises the floor to REQUIRED_SCHEMA makes this test require the module, so the job runs
    them rather than skipping."""
    extras = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["optional-dependencies"]
    (req,) = [r for r in extras["attestation"] if r.startswith("attestation-schema")]
    floor = re.search(r">=\s*([\d.]+)", req)
    assert floor, req
    if _version(floor[1]) >= _version(DR.REQUIRED_SCHEMA):
        assert DR.AVAILABLE, DR.not_installed()
        import pyarrow  # noqa: F401  (the `dataset` extra, which the test job installs beside `attestation`)


def test_without_the_release_every_entry_point_names_it(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(DR, "AVAILABLE", False)
    with pytest.raises(DR.DatasetError, match=re.escape(f"attestation-schema >= {DR.REQUIRED_SCHEMA}")):
        DR.export_dataset_dir([PREBUILT / "benzoic-rhf" / "run"], tmp_path / "ds")
    with pytest.raises(DR.DatasetError, match="attestation.dataset/1"):
        DR.open_dataset(tmp_path)
    assert cli.main(["export", "dataset", str(PREBUILT / "benzoic-rhf" / "run"), "-o", str(tmp_path / "ds")]) == 2
    assert DR.REQUIRED_SCHEMA in capsys.readouterr().err
    assert not (tmp_path / "ds").exists()


# ---- fixtures ------------------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def schema():
    if not DR.AVAILABLE:
        pytest.skip(DR.not_installed())
    pytest.importorskip("pyarrow")
    pytest.importorskip("rdkit")  # the scaffold split
    import attestation_schema

    return attestation_schema


@pytest.fixture(scope="module")
def runs(schema, tmp_path_factory):
    """Copies of the prebuilt runs, each with its core record (`smeltery record core`)."""
    from smeltery import core_record

    root = tmp_path_factory.mktemp("runs")
    out = {}
    for name in (*CONSISTENT, MIXED):
        d = root / name
        shutil.copytree(PREBUILT / name / "run", d)
        assert core_record.main(["core", str(d)]) == 0
        out[name] = d
    return out


@pytest.fixture(scope="module")
def exported(runs, tmp_path_factory):
    dest = tmp_path_factory.mktemp("datasets") / "smeltery-mwe" / "1"
    DR.export_dataset_dir(
        [runs[n] for n in CONSISTENT],
        dest,
        dataset_id="smeltery-mwe",
        version="1",
        splits=[DR.SplitSpec("scaffold", 0, TWO)],
    )
    return dest


def _tree(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _training_record(schema, data: list[dict], run_id: str = "train-1"):
    """A training run's core record: what a script that trained on the dataset would write."""
    return schema.from_dict(
        {
            "schema": schema.SCHEMA,
            "run_id": run_id,
            "family": "ddE-model",
            "arm": None,
            "status": "complete",
            "started_at": None,
            "finished_at": None,
            "producer": {"name": "test-trainer", "version": "1.0"},
            "code": {
                "git_sha": None,
                "dirty": None,
                "repo": None,
                "version": "1.0",
                "provenance": "inferred",
                "evidence": "a test",
            },
            "config": {},
            "data": data,
            "metrics": [],
            "history": None,
            "seeds": None,
            "dependencies": [],
            "ext": {},
        }
    )


# ---- the dataset directory ---------------------------------------------------------------------------------------


def test_the_directory_conforms_and_holds_table_split_manifest_record(schema, exported):
    from attestation_schema import conformance

    report = conformance.check_dir(exported)
    assert report.ok, report.format()
    assert sorted(_tree(exported)) == [
        "attestation/dataset.json",
        "attestation/digests.json",
        "runs.parquet",
        "splits/scaffold-seed0.json",
    ]
    record = report.dataset
    digests = (exported / "attestation" / "digests.json").read_bytes()
    assert record.content_sha256 == hashlib.sha256(digests).hexdigest()  # `sha256sum digests.json` checks it
    assert (record.dataset_id, record.version, record.schema) == ("smeltery-mwe", "1", "attestation.dataset/1")
    assert [(f.path, f.media_type) for f in record.files] == [
        ("runs.parquet", "application/vnd.apache.parquet"),
        ("splits/scaffold-seed0.json", "application/json"),
    ]
    (split,) = record.splits
    assert (split.name, split.path, split.kind, split.seed) == (
        "scaffold-seed0",
        "splits/scaffold-seed0.json",
        "scaffold",
        0,
    )
    doc = json.loads((exported / split.path).read_text())
    assert doc["table"]["sha256"] == S.table_sha256(exported / "runs.parquet")  # the split names the table inside
    assert record.producer.name == "smeltery export" and record.created_at is not None
    assert not list(exported.parent.glob(".*staging*"))  # nothing left from the staging directory


def test_the_license_is_null_unless_stated_and_never_smelterys_own(schema, exported, runs, tmp_path):
    record = schema.read_dataset(exported)
    assert record.license is None
    own = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["license"]["text"]
    assert own == "MIT OR Apache-2.0" and record.license != own
    with pytest.raises(DR.DatasetError, match="SPDX"):
        DR.export_dataset_dir([runs["benzoic-rhf"]], tmp_path / "bad", license="NOASSERTION")
    assert not (tmp_path / "bad").exists()
    DR.export_dataset_dir([runs["benzoic-rhf"]], tmp_path / "ok", license="CC-BY-4.0")
    assert schema.read_dataset(tmp_path / "ok").license == "CC-BY-4.0"


def test_columns_come_from_arrow_schema_with_a_unit_only_where_one_holds(schema, exported):
    record = schema.read_dataset(exported)
    assert [c.name for c in record.columns] == D.arrow_schema().names
    assert [c.type for c in record.columns] == [str(f.type) for f in D.arrow_schema()]
    units = {c.name: c.unit for c in record.columns if c.unit is not None}
    assert units == {
        n: "kcal/mol" for n in ("score", "score_sem", "score_floor", "paired_ddE", "paired_ddE_sem", "paired_ddE_floor")
    }
    import pyarrow.parquet as pq

    assert record.rows == pq.read_table(exported / "runs.parquet").num_rows
    ext = record.ext["smeltery"]
    assert ext["schema_version"] == D.SCHEMA_VERSION == 3 and ext["grain"] == D.GRAIN
    assert ext["table"] == {"path": "runs.parquet", "format": "parquet"}
    assert ext["splits"]["scaffold-seed0"]["parameters"]["fractions"] == TWO
    assert ext["splits"]["scaffold-seed0"]["algorithm_version"] == S.ALGORITHM_VERSIONS["scaffold"]
    assert set(ext["writer"]) == {"pyarrow"} and ext["units"]["dE_int"] == "kcal/mol"


def test_source_runs_are_the_core_records_by_digest(schema, exported, runs):
    record = schema.read_dataset(exported)
    want = []
    for n in CONSISTENT:
        raw = (runs[n] / "attestation" / "run.json").read_bytes()
        core = schema.read(runs[n])
        assert schema.record_digest(core) == hashlib.sha256(raw).hexdigest()
        want.append((core.run_id, core.family, hashlib.sha256(raw).hexdigest()))
    assert [(r.run_id, r.family, r.record_sha256) for r in record.source_runs] == want
    import pyarrow.parquet as pq

    table = pq.read_table(exported / "runs.parquet").to_pylist()
    assert {r["core_record_sha256"] for r in table} == {w[2] for w in want}  # the same digest, row by row


def test_a_training_run_resolves_through_the_dataset_to_the_ferric_build(schema, exported, runs):
    """The end-to-end claim: no broken and no unverified link, from the training run to each source run's ferric."""
    from smeltery.dataset_torch import ExportDataset

    record = schema.read_dataset(exported)
    train = ExportDataset(
        exported,
        features=["score_n"],
        target="score",
        split="scaffold-seed0",
        partition="train",
        tensors=False,
        dataset_sha256=record.content_sha256,
    )
    data = train.lineage_entries()
    assert data == [
        {"name": "smeltery-mwe", "sha256": record.content_sha256, "role": "train"},
        {"name": "smeltery-mwe/splits/scaffold-seed0.json", "sha256": record.splits[0].sha256, "role": "split"},
    ]
    training = _training_record(schema, data)
    sources = [schema.read(runs[n]) for n in CONSISTENT]
    lin = schema.resolve_lineage(training, [record], sources)
    assert lin.verified and lin.ok, lin.format()
    ds_key = f"dataset:{schema.dataset_digest(record)}"
    assert [(e.source, e.via) for e in lin.upstream(lin.root)] == [(ds_key, "content"), (ds_key, "split")]
    assert [e.source for e in lin.upstream(ds_key)] == [f"run:{schema.record_digest(r)}" for r in sources]
    # ... to ferric: every source run's verified build, and the table's column agrees with it row by row
    builds = {lin.node(k).record.run_id: d for k, d in lin.dependencies("ferric")}
    assert set(builds) == {r.run_id for r in sources}
    assert {(d.provenance, d.build_sha is not None) for d in builds.values()} == {("verified", True)}
    import pyarrow.parquet as pq

    for row in pq.read_table(exported / "runs.parquet").to_pylist():
        assert row["ferric_git_sha"] == builds[row["run_id"]].build_sha, row["run_id"]


def test_a_job_runs_own_run_id_is_what_the_dataset_names(schema, runs, tmp_path):
    from smeltery import core_record

    job = tmp_path / "job"
    shutil.copytree(PREBUILT / "benzoic-rhf" / "run", job)
    (legacy,) = job.glob("run-*.json")
    core_record.write_core(legacy, run_id="campaign-job-42")
    DR.export_dataset_dir([job], tmp_path / "ds")
    record = schema.read_dataset(tmp_path / "ds")
    assert [r.run_id for r in record.source_runs] == ["campaign-job-42"]
    lin = schema.resolve_lineage(
        _training_record(schema, DR.open_dataset(tmp_path / "ds").lineage_entries()), [record], [schema.read(job)]
    )
    assert lin.verified, lin.format()


def test_a_run_without_a_core_record_is_named_but_not_digested(schema, tmp_path):
    DR.export_dataset_dir([PREBUILT / "benzoic-rhf" / "run"], tmp_path / "ds")
    record = schema.read_dataset(tmp_path / "ds")
    (src,) = record.source_runs
    assert src.record_sha256 is None and src.family == "mwe-benzoic-cli"
    # no run record exists to link it to: the resolver says so rather than inventing a link
    training = _training_record(schema, DR.open_dataset(tmp_path / "ds").lineage_entries())
    lin = schema.resolve_lineage(training, [record])
    assert [(i.kind, i.at) for i in lin.issues] == [("missing-source-run", "source_runs[0]")]


def _builds_shas(run_dir: Path) -> set[str]:
    legacy = json.loads(next(run_dir.glob("run-*.json")).read_text())
    return {b["ferric"]["git_sha"] for b in legacy["results"]["builds"] if "ferric" in b}


def test_the_mixed_build_run_exports_with_its_rows_builds_unrecorded_and_reported(schema, runs, tmp_path):
    """danuglipron-halogen: its core record says ferric `unknown` (b22183b wrote 111 rows, 81f411c 1, 46 carry no
    stamp), and every candidate has unstamped SCFs, so no row can claim a build: null sha, UNKNOWN, `unrecorded` --
    not the finalizing build the legacy block names. The export passes the cross-check and the conformance check."""
    from attestation_schema import conformance

    core = schema.read(runs[MIXED])
    (ferric,) = [d for d in core.dependencies if d.name == "ferric"]
    assert ferric.build_sha is None and ferric.provenance == "unknown" and "mixed builds" in ferric.evidence
    dest = tmp_path / "ds"
    DR.export_dataset_dir([runs[MIXED]], dest, dataset_id="x")
    assert conformance.check_dataset_dir(dest).ok
    import pyarrow.parquet as pq

    cols = pq.read_table(dest / "runs.parquet").to_pydict()
    assert len(cols["ligand"]) == 13
    assert set(cols["ferric_git_sha"]) == {None} and set(cols["ferric_dirty"]) == {None}
    assert set(cols["ferric_build_basis"]) == {"unrecorded"} and set(cols["ferric_provenance_level"]) == {"UNKNOWN"}
    assert set(cols["ferric_version"]) == {None}
    assert "4 of 12 SCF rows" in cols["ferric_provenance"][0] and "b22183b9dbed" in cols["ferric_provenance"][0]
    rows = schema.read_dataset(dest).ext["smeltery"]["ferric_rows"]
    assert rows == {"rows": 13, "by_build": {}, "unknown": 13, "mixed": 0}


@pytest.fixture(scope="module")
def stamped(schema, tmp_path_factory):
    """The mixed-build run with every unstamped SCF row stamped as b22183b (so most candidates have ONE recorded
    build), its core record written after: pyr-C28-Cl keeps its 81f411c row, so its own SCFs are from two builds."""
    from smeltery import core_record

    d = tmp_path_factory.mktemp("stamped") / MIXED
    shutil.copytree(PREBUILT / MIXED / "run", d)
    lines = [json.loads(x) for x in (d / "calcs.jsonl").read_text().splitlines()]
    stamp = next(r["build"] for r in lines if "build" in r and r["build"]["ferric"]["git_sha"].startswith("b22183b"))
    for r in lines:
        r.setdefault("build", stamp)
    (d / "calcs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in lines))
    assert core_record.main(["core", str(d)]) == 0
    return d


def test_each_row_states_the_build_of_its_own_scfs_matching_results_builds(schema, stamped, tmp_path):
    from attestation_schema import conformance

    dest = tmp_path / "ds"
    DR.export_dataset_dir([stamped], dest, dataset_id="x")
    assert conformance.check_dataset_dir(dest).ok
    import pyarrow.parquet as pq

    cols = pq.read_table(dest / "runs.parquet").to_pydict()
    named = _builds_shas(stamped)
    assert named == {"81f411c94330f09a21a18b71a02544c518703dbd", "b22183b9dbed226e1d2acc5cb6e5194c973b023b"}
    for lig, sha, dirty, basis, level in zip(
        cols["ligand"],
        cols["ferric_git_sha"],
        cols["ferric_dirty"],
        cols["ferric_build_basis"],
        cols["ferric_provenance_level"],
        strict=True,
    ):
        if lig == "pyr-C28-Cl":  # two builds measured this candidate: none can be claimed
            assert (sha, dirty, basis, level) == (None, None, "mixed_scf_rows", "UNKNOWN")
        else:
            assert sha == "b22183b9dbed226e1d2acc5cb6e5194c973b023b" and dirty is False
            assert (basis, level) == ("scf_rows", "VERIFIED") and sha in named
    ext = schema.read_dataset(dest).ext["smeltery"]["ferric_rows"]
    assert ext == {"rows": 13, "by_build": {"b22183b9dbed226e1d2acc5cb6e5194c973b023b": 12}, "unknown": 0, "mixed": 1}


def test_a_row_claiming_a_build_the_run_never_used_is_refused_naming_row_and_run(schema, stamped):
    rows = D.export_rows([stamped])
    _, cores, listed = DR._source_runs([stamped])
    assert DR.check_ferric(rows, cores, listed)["mixed"] == 1  # as exported: accepted
    foreign = "f" * 40
    bad = [dict(r) for r in rows]
    bad[3]["ferric_git_sha"] = foreign
    with pytest.raises(DR.DatasetError) as e:
        DR.check_ferric(bad, cores, listed)
    msg = str(e.value)
    assert "row 3 (" in msg and foreign in msg and "run " in msg and "results.builds" in msg
    assert "row 0 (" not in msg


def test_a_single_build_row_must_still_equal_the_core_records_ferric_exactly(schema, runs):
    rows = D.export_rows([runs["benzoic-rhf"]])
    _, cores, listed = DR._source_runs([runs["benzoic-rhf"]])
    assert {r["ferric_build_basis"] for r in rows} == {"run_record"}
    assert DR.check_ferric(rows, cores, listed) == {
        "rows": 3,
        "by_build": rows[0]["ferric_git_sha"] and {rows[0]["ferric_git_sha"]: 3},
        "unknown": 0,
        "mixed": 0,
    }
    for sha in (None, "f" * 40):  # a null does not excuse a run that states one build
        bad = [{**r, "ferric_git_sha": sha} for r in rows]
        with pytest.raises(DR.DatasetError, match="disagree on ferric"):
            DR.check_ferric(bad, cores, listed)


# ---- immutability and versions -------------------------------------------------------------------------------------


def test_an_existing_dataset_directory_is_never_written_over(schema, exported, runs, tmp_path):
    before = _tree(exported)
    with pytest.raises(DR.DatasetError, match="immutable"):
        DR.export_dataset_dir([runs["benzoic-rhf"]], exported)
    with pytest.raises(S.SplitError, match="immutable"):
        S.write_split(S.make_split(exported / "runs.parquet", "target", fractions=TWO), exported / "splits" / "t.json")
    with pytest.raises(DR.DatasetError, match="overlap"):
        DR.new_version(exported, exported / "v2", split=DR.SplitSpec("target", 0, TWO))
    from attestation_schema import dataset as ds
    from attestation_schema import layout

    with pytest.raises(layout.LayoutError):
        ds.write_dataset_manifest(exported)
    assert _tree(exported) == before
    (tmp_path / "empty").mkdir()  # an empty directory is a fresh destination
    DR.export_dataset_dir([runs["benzoic-rhf"]], tmp_path / "empty")
    assert schema.read_dataset(tmp_path / "empty").rows == 3


def test_a_later_split_is_a_new_version_and_the_old_one_still_resolves(schema, exported, runs, tmp_path):
    before = _tree(exported)
    old = schema.read_dataset(exported)
    v2 = exported.parent / "2"
    assert (
        cli.main(["export", "split", "target", str(exported), "-o", str(v2), "--fractions", "train=0.5,test=0.5"]) == 0
    )
    assert _tree(exported) == before  # the old version is untouched, byte for byte
    from attestation_schema import conformance

    report = conformance.check_dir(v2)
    assert report.ok, report.format()
    new = report.dataset
    assert (new.dataset_id, new.version) == (old.dataset_id, "2")
    assert [s.name for s in new.splits] == ["scaffold-seed0", "target-seed0"]
    assert new.content_sha256 != old.content_sha256 and new.source_runs == old.source_runs
    assert _tree(v2)["runs.parquet"] == before["runs.parquet"]  # the same table bytes, so old splits still load
    assert new.ext["smeltery"]["derived_from"] == {
        "dataset_sha256": schema.dataset_digest(old),
        "content_sha256": old.content_sha256,
        "version": "1",
        "added": ["splits/target-seed0.json"],
    }
    sources = [schema.read(runs[n]) for n in CONSISTENT]
    # a run trained on version 1 keeps resolving to version 1 by content digest, with version 2 beside it
    v1_key, v2_key = f"dataset:{schema.dataset_digest(old)}", f"dataset:{schema.dataset_digest(new)}"
    on_v1 = _training_record(schema, DR.open_dataset(exported).lineage_entries(), "on-v1")
    lin = schema.resolve_lineage(on_v1, [old, new], sources)
    assert lin.verified, lin.format()
    assert [(e.source, e.via) for e in lin.upstream(lin.root)] == [(v1_key, "content")]
    # SCHEMA FINDING (draft dataset/1): a split file carried into the new version has the same sha256 in both, and
    # the resolver matches each data[] entry on its own, so the run's split entry matches BOTH versions and is
    # reported `ambiguous` although its content entry already pins version 1. Asserted as found, not worked around.
    on_v1_split = _training_record(schema, DR.open_dataset(exported).lineage_entries(split="scaffold-seed0"), "v1s")
    lin = schema.resolve_lineage(on_v1_split, [old, new], sources)
    assert lin.ok and [(i.kind, i.at) for i in lin.issues] == [("ambiguous", "data[1]")], lin.format()
    assert [(e.source, e.via) for e in lin.upstream(lin.root)] == [
        (v1_key, "content"),
        (v1_key, "split"),
        (v2_key, "split"),
    ]
    on_v2 = _training_record(schema, DR.open_dataset(v2).lineage_entries(split="target-seed0"), "on-v2")
    lin2 = schema.resolve_lineage(on_v2, [old, new], sources)
    assert lin2.verified and {e.source for e in lin2.upstream(lin2.root)} == {v2_key}
    # the same split twice, or a destination that exists, is refused; a non-numeric version needs --version
    with pytest.raises(DR.DatasetError, match="already has split"):
        DR.new_version(v2, tmp_path / "v3", split=DR.SplitSpec("target", 0, TWO))
    with pytest.raises(DR.DatasetError, match="immutable"):
        DR.new_version(exported, v2, split=DR.SplitSpec("target", 1, TWO))
    assert DR.next_version("v9") == "v10" and DR.next_version("2026-11-19") is None
    assert cli.main(["export", "split", "verify", "target-seed0", str(v2)]) == 0


# ---- reading a dataset directory -----------------------------------------------------------------------------------


def test_export_dataset_reads_a_dataset_directory_only_after_checking_it(schema, exported, tmp_path):
    from smeltery.dataset_torch import ExportDataset

    ds = ExportDataset(
        exported,
        features=["score_n"],
        target="score",
        split="splits/scaffold-seed0.json",
        partition="test",
        tensors=False,
    )
    split = json.loads((exported / "splits" / "scaffold-seed0.json").read_text())
    assert sorted(ds.identity(i) for i in range(len(ds))) == sorted(tuple(r) for r in split["partitions"]["test"])
    with pytest.raises(DR.DatasetError, match="not the expected"):
        ExportDataset(exported, features=["score_n"], target="score", tensors=False, dataset_sha256="0" * 64)
    with pytest.raises(S.SplitError, match="no split"):
        ExportDataset(exported, features=["score_n"], target="score", split="nope", partition="train", tensors=False)
    with pytest.raises(ValueError, match="dataset directory"):
        ExportDataset(exported / "runs.parquet", features=["score_n"], target="score", tensors=False).lineage_entries()
    tampered = tmp_path / "tampered"
    shutil.copytree(exported, tampered)
    (tampered / "splits" / "scaffold-seed0.json").write_text("{}\n")
    with pytest.raises(DR.DatasetError, match="does not match its dataset record"):
        ExportDataset(tampered, features=["score_n"], target="score", tensors=False)
    assert cli.main(["export", "split", "verify", "scaffold-seed0", str(tampered)]) == 2


def test_cli_writes_a_dataset_directory(schema, runs, tmp_path, capsys):
    dest = tmp_path / "ds"
    argv = ["export", "dataset", str(runs["benzoic-rhf"]), "-o", str(dest), "--format", "arrow", "--version", "v1"]
    assert cli.main([*argv, "--split", "target:3", "--fractions", "train=0.5,test=0.5", "--license", "CC0-1.0"]) == 2
    assert "cannot fill" in capsys.readouterr().err and not dest.exists()  # one target: the split refuses
    assert cli.main([*argv, "--license", "CC0-1.0"]) == 0
    out = capsys.readouterr().out
    record = schema.read_dataset(dest)
    assert f"content sha256 {record.content_sha256}" in out
    assert [f.path for f in record.files] == ["runs.arrow"] and record.license == "CC0-1.0"
    assert record.dataset_id == "mwe-benzoic-cli"  # the campaign, by default
    assert cli.main(argv) == 2 and "immutable" in capsys.readouterr().err
