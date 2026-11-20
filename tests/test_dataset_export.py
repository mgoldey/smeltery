"""`smeltery.dataset_export`: one row per (ligand, target, tier), checked against the prebuilt runs' own JSON.

The row builder needs only the core install, so those tests always run; the file tests need pyarrow (the `dataset`
extra) and skip without it. The golden runs are the committed `src/smeltery/prebuilt/*/run/` records, so the
expected values are read from the record, never typed in."""

import json
import shutil
from pathlib import Path

import pytest

from smeltery import cli
from smeltery import dataset_export as D
from smeltery.decisions import DecisionSet
from smeltery.record import digest

PREBUILT = Path(__file__).resolve().parents[1] / "src" / "smeltery" / "prebuilt"
RHF = PREBUILT / "benzoic-rhf" / "run"
PBE = PREBUILT / "benzoic-pbe" / "run"
DOCKING = PREBUILT / "docking" / "run"
STRUCTURE = PREBUILT / "structure" / "run"
DANUGLIPRON = PREBUILT / "danuglipron-halogen" / "run"
ALL_RUNS = [RHF, PBE, DOCKING, STRUCTURE, DANUGLIPRON]


def record_of(run_dir: Path) -> dict:
    (path,) = run_dir.glob("run-*.json")
    return json.loads(path.read_text())


def mean(xs):
    return sum(xs) / len(xs)


@pytest.fixture
def pa():
    return pytest.importorskip("pyarrow")


def test_the_cut_quantity_is_the_one_the_run_cuts_on():
    assert D.CUT_QUANTITY == cli.QUANTITY


def test_golden_rows_of_the_rhf_run_match_its_json():
    rec = record_of(RHF)
    rows = D.export_rows([RHF])
    # three candidates x the one tier that declares a score (paired_poses writes poses only)
    assert [(r["ligand"], r["tier"]) for r in rows] == [
        ("benzoic", "field_interaction"),
        ("4-F", "field_interaction"),
        ("4-Cl", "field_interaction"),
    ]
    assert {r["tier"] for r in rows}.isdisjoint({t["name"] for t in rec["tiers"] if t["name"] == "paired_poses"})
    for r in rows:
        values = rec["results"]["per_pose"][r["ligand"]]["dE_int"]
        assert r["score_quantity"] == "dE_int" and r["score_unit"] == rec["results"]["units"]["dE_int"]
        assert r["score"] == pytest.approx(mean(values))
        assert r["score_n"] == len(values)
        assert r["smiles_recorded"] == rec["inputs"]["candidates"][r["ligand"]]
        assert r["smiles_form"] == "rdkit-canonical"
        assert r["is_parent"] == (r["ligand"] == rec["inputs"]["parent"])
        # provenance, copied from the record
        assert r["run_id"] == rec["input_digest"][:12] and r["input_digest"] == rec["input_digest"]
        assert r["record_file"] == f"run-{r['run_id']}.json"
        assert r["record_sha256"] == rec.get("record_sha256")
        assert r["smeltery_version"] == rec["smeltery_version"]
        assert r["ferric_version"] == rec["ferric"]["version"]
        assert r["ferric_git_sha"] == rec["ferric"]["commit"]
        assert r["ferric_dirty"] is rec["ferric"]["dirty"]
        assert r["ferric_provenance"] == rec["ferric"]["provenance"]
        assert r["ferric_provenance_level"] == "VERIFIED"
    parent, f, cl = rows
    pd = rec["results"]["paired_ddE"]
    assert parent["paired_ddE"] is None and parent["decision"] == "reference"
    assert f["paired_ddE"] == pytest.approx(pd["4-F"]["mean"]) and f["paired_ddE_sem"] == pytest.approx(
        pd["4-F"]["sem"]
    )
    assert f["paired_ddE_floor"] == rec["inputs"]["floor"]["value"]
    assert f["paired_ddE_floor_source"] == rec["inputs"]["floor"]["source"]
    assert (f["decision"], cl["decision"]) == ("pass", "drop")  # results.cut.survivors == ["4-F"]
    # the tier's own floor is unmeasured: null, with the reason, not zero
    assert f["score_floor"] is None and "systematic_floor() is None" in f["score_floor_source"]
    ddE = [s for s in f["scores"] if s["kind"] == "paired_vs_parent"]
    assert [(s["quantity"], s["unit"], s["n"]) for s in ddE] == [("ddE", "kcal/mol", pd["4-F"]["n"])]


def test_the_target_is_what_the_record_says_it_was_measured_against():
    rec = record_of(RHF)
    (target,) = {(r["target"], r["target_kind"], r["target_sha256"]) for r in D.export_rows([RHF])}
    assert target == (f"field:{digest(rec['inputs']['field'])[:12]}", "field", digest(rec["inputs"]["field"]))
    docking = record_of(DOCKING)["inputs"]["pocket"]
    (target,) = {(r["target"], r["target_kind"], r["target_sha256"]) for r in D.export_rows([DOCKING])}
    assert target == (docking["input"], "pocket", docking["input_sha256"])
    spec = record_of(STRUCTURE)["inputs"]["structure"]
    (target,) = {(r["target"], r["target_kind"]) for r in D.export_rows([STRUCTURE])}
    assert target == (spec["spec"], "structure")


def test_every_quantity_a_tier_declares_gets_its_unit_from_the_record_and_one_tier():
    for run_dir in ALL_RUNS:
        rec = record_of(run_dir)
        rows = D.export_rows([run_dir])
        got = {}
        for r in rows:
            for s in r["scores"]:
                if s["kind"] == "per_pose_mean":
                    assert got.setdefault(s["quantity"], r["tier"]) == r["tier"], (run_dir, s["quantity"])
                    assert s["unit"] == rec["results"]["units"][s["quantity"]]
        assert set(got) == set(rec["results"]["units"]), run_dir  # nothing recorded is dropped or invented
        assert "unattributed" not in {r["tier"] for r in rows}, run_dir
        keys = [(r["ligand"], r["tier"]) for r in rows]
        assert len(keys) == len(set(keys))  # one row per (ligand, tier) within a run, and one target per run


def test_a_remedy_ladder_in_the_recorded_settings_brings_its_scf_rung_column():
    quantities = {s["quantity"] for r in D.export_rows([DANUGLIPRON]) for s in r["scores"]}
    assert {"dE_int", "scf_rung", "ddE"} <= quantities  # FieldInteraction declares scf_rung only with remedies


def test_a_tier_that_declares_a_floor_reports_it_from_the_tier_not_from_this_module():
    (d,) = D.tier_declarations({"name": "gfn2"}, {})
    assert (d.quantity, d.unit, d.floor, d.floor_source) == (
        "E_gfn2",
        "kcal/mol",
        pytest.approx(0.825),
        "tier gfn2 systematic_floor",
    )


def test_a_tier_that_needs_a_provider_falls_back_to_its_quantity_and_says_the_floor_is_unresolved():
    (d,) = D.tier_declarations({"name": "docking", "provider": "vina"}, {"dock_score": "kcal/mol"})
    assert (d.quantity, d.unit, d.floor) == ("dock_score", "kcal/mol", None)
    assert "not resolved" in d.floor_source
    assert D.tier_declarations({"name": "no-such-tier"}, {}) == []


def test_smiles_are_canonical_unless_asked_not_to_and_the_form_says_which():
    canon = D.export_rows([RHF])
    raw = D.export_rows([RHF], canonicalize=False)
    assert {r["smiles"] for r in canon} == {"O=C(O)c1ccccc1", "O=C(O)c1ccc(F)cc1", "O=C(O)c1ccc(Cl)cc1"}
    assert {r["smiles_form"] for r in canon} == {"rdkit-canonical"}
    assert [r["smiles"] for r in raw] == [r["smiles_recorded"] for r in raw]
    assert {r["smiles_form"] for r in raw} == {"as-recorded"}
    assert D._canonical("not a smiles ((", True) == ("not a smiles ((", "as-recorded")


def test_unranked_runs_are_undecided_not_dropped():
    decisions = {r["ligand"]: r["decision"] for r in D.export_rows([DOCKING]) if r["tier"] == "field_interaction"}
    assert decisions == {"ethanol": "reference", "propanol": "undecided", "butanol": "undecided"}


def test_the_same_run_twice_and_an_empty_directory_are_refused(tmp_path):
    with pytest.raises(D.ExportError, match="the same run"):
        D.export_rows([RHF, RHF / f"run-{record_of(RHF)['input_digest'][:12]}.json"])
    with pytest.raises(D.ExportError, match="no run-"):
        D.export_rows([tmp_path])
    with pytest.raises(D.ExportError, match="no such"):
        D.export_rows([tmp_path / "nope"])


def test_a_decisions_file_beside_the_record_adds_the_users_decision(tmp_path):
    out = tmp_path / "out"
    shutil.copytree(RHF, out, ignore=shutil.ignore_patterns("poses"))
    rec = record_of(out)
    ds = DecisionSet(campaign=rec["campaign"], run=rec["input_digest"], candidates=rec["inputs"]["candidates"])
    ds.set("4-F", "keep", "cut_survivor", user="t")
    ds.dump(out / f"decisions-{rec['input_digest'][:12]}.json")
    got = {r["ligand"]: (r["user_decision"], r["user_decision_reason"]) for r in D.export_rows([out])}
    assert got == {"benzoic": (None, None), "4-F": ("keep", "cut_survivor"), "4-Cl": (None, None)}
    assert {r["user_decision"] for r in D.export_rows([RHF])} == {None}


# ---- the files (pyarrow) ----------------------------------------------------------------------------------------


def test_golden_parquet_file_has_the_columns_rows_provenance_and_metadata(tmp_path, pa):
    import pyarrow.parquet as pq

    dest = tmp_path / "rhf.parquet"
    assert D.write_dataset([RHF], dest) == 3
    rec = record_of(RHF)
    table = pq.read_table(dest)
    assert table.schema.names == D.arrow_schema().names
    assert table.num_rows == 3
    col = table.to_pydict()
    assert set(col["run_id"]) == {rec["input_digest"][:12]}
    assert set(col["ferric_git_sha"]) == {rec["ferric"]["commit"]}
    assert set(col["ferric_dirty"]) == {rec["ferric"]["dirty"]}
    assert set(col["ferric_provenance_level"]) == {"VERIFIED"}
    assert set(col["smeltery_version"]) == {rec["smeltery_version"]}
    assert set(col["record_sha256"]) == {None}  # this record predates the self-hash: null, not invented
    # no core record beside it: no digest, and the identity a core record would state for a run given no name
    assert set(col["core_record_sha256"]) == {None}
    assert set(col["family"]) == {rec["campaign"]}
    assert col["tier"] == ["field_interaction"] * 3
    assert col["decision"] == ["reference", "pass", "drop"]
    nested = col["scores"][1]
    assert [s["quantity"] for s in nested] == ["dE_int", "ddE"]
    meta = {k.decode(): v.decode() for k, v in table.schema.metadata.items() if k.startswith(b"smeltery.")}
    assert meta["smeltery.schema_version"] == str(D.SCHEMA_VERSION) == "3"
    assert json.loads(meta["smeltery.units"]) == {"dE_int": "kcal/mol", "ddE": "kcal/mol"}
    assert meta["smeltery.smiles_form"] == "rdkit-canonical"
    assert json.loads(meta["smeltery.runs"]) == [rec["input_digest"][:12]]


def test_the_record_sha256_is_carried_when_the_record_has_one(tmp_path, pa):
    out = tmp_path / "out"
    shutil.copytree(RHF, out, ignore=shutil.ignore_patterns("poses"))
    (path,) = out.glob("run-*.json")
    rec = json.loads(path.read_text())
    rec["record_sha256"] = digest({k: v for k, v in rec.items() if k != "record_sha256"})
    path.write_text(json.dumps(rec))
    assert {r["record_sha256"] for r in D.export_rows([out])} == {rec["record_sha256"]}


def test_runs_concatenate_with_one_schema_whatever_each_run_contains(tmp_path, pa):
    import pyarrow.parquet as pq

    per_run = {}
    for run_dir in ALL_RUNS:
        dest = tmp_path / f"{run_dir.parent.name}.parquet"
        D.write_dataset([run_dir], dest)
        per_run[run_dir.parent.name] = pq.read_table(dest)
    schemas = {name: t.schema.remove_metadata() for name, t in per_run.items()}
    assert len({str(s) for s in schemas.values()}) == 1, "every run's file must carry the same schema"
    assert schemas["benzoic-rhf"].equals(D.arrow_schema().remove_metadata())

    both = tmp_path / "all.parquet"
    n = D.write_dataset(ALL_RUNS, both)
    table = pq.read_table(both)
    assert table.num_rows == n == sum(t.num_rows for t in per_run.values())
    assert table.schema.remove_metadata().equals(schemas["benzoic-rhf"])
    assert len(set(table["run_id"].to_pylist())) == len(ALL_RUNS)
    meta = {k.decode(): v.decode() for k, v in table.schema.metadata.items()}
    units = json.loads(meta["smeltery.units"])
    assert units["scf_rung"] == "rung" and units["dock_score"] == "kcal/mol" and units["E_d3"] == "kcal/mol"
    assert len(json.loads(meta["smeltery.runs"])) == len(ALL_RUNS)


def test_arrow_ipc_carries_the_same_table_and_metadata(tmp_path, pa):
    import pyarrow.ipc as ipc

    dest = tmp_path / "rhf.arrow"
    assert D.write_dataset([RHF], dest, "arrow") == 3
    with ipc.open_file(str(dest)) as f:
        table = f.read_all()
    assert table.schema.names == D.arrow_schema().names
    assert table.schema.metadata[b"smeltery.schema_version"] == b"3"
    assert table.to_pylist() == D.to_table(D.export_rows([RHF])).to_pylist()


def test_nothing_is_left_behind_when_the_export_fails(tmp_path, pa):
    dest = tmp_path / "x.parquet"
    with pytest.raises(D.ExportError):
        D.write_dataset([tmp_path / "nope"], dest)
    with pytest.raises(D.ExportError, match="unknown format"):
        D.write_dataset([RHF], dest, "csv")
    assert list(tmp_path.iterdir()) == []


# ---- the CLI ------------------------------------------------------------------------------------------------------


def test_cli_exports_parquet_and_arrow(tmp_path, capsys, pa):
    import pyarrow.parquet as pq

    dest = tmp_path / "o.parquet"
    assert cli.main(["export", "parquet", str(RHF), str(PBE), "-o", str(dest)]) == 0
    assert "wrote 6 rows" in capsys.readouterr().out
    assert pq.read_table(dest).num_rows == 6
    assert cli.main(["export", "arrow", str(RHF), "-o", str(tmp_path / "o.arrow"), "--no-canonical-smiles"]) == 0
    assert (tmp_path / "o.arrow").is_file()


def test_cli_refuses_with_a_message_not_a_traceback(tmp_path, capsys):
    assert cli.main(["export", "parquet", str(tmp_path / "nope"), "-o", str(tmp_path / "o.parquet")]) == 2
    assert "smeltery export:" in capsys.readouterr().err


def test_without_pyarrow_the_error_names_the_extra(tmp_path, capsys, monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "pyarrow", None)
    assert cli.main(["export", "parquet", str(RHF), "-o", str(tmp_path / "o.parquet")]) == 2
    err = capsys.readouterr().err
    assert "smeltery[dataset]" in err
    assert not (tmp_path / "o.parquet").exists()
    assert len(D.export_rows([RHF])) == 3  # the row builder needs no pyarrow


# ---- the core record's identity and digest (schema_version 2) -------------------------------------------------------


def _with_core(tmp_path, run_dir=RHF, run_id=None):
    """A copy of a prebuilt run with its core record written (`smeltery record core`), optionally as a job's run_id."""
    core_record = pytest.importorskip("smeltery.core_record")
    if not core_record.AVAILABLE:
        pytest.skip(core_record.NOT_INSTALLED)
    out = tmp_path / run_dir.parent.name
    shutil.copytree(run_dir, out)
    (legacy,) = out.glob("run-*.json")
    core_record.write_core(legacy, run_id=run_id)
    return out


def test_run_id_family_and_core_digest_come_from_the_core_record(tmp_path):
    import hashlib

    out = _with_core(tmp_path, run_id="job-7")  # a job run: the submitter's run_id, not input_digest[:12]
    rows = D.export_rows([out])
    core = json.loads((out / "attestation" / "run.json").read_bytes())
    want = hashlib.sha256((out / "attestation" / "run.json").read_bytes()).hexdigest()
    assert {(r["run_id"], r["family"], r["core_record_sha256"]) for r in rows} == {("job-7", core["family"], want)}
    rec = record_of(RHF)
    assert {r["input_digest"][:12] for r in rows} == {rec["input_digest"][:12]}  # the old run_id stays derivable
    assert {r["record_file"] for r in rows} == {f"run-{rec['input_digest'][:12]}.json"}


def test_a_stale_core_record_is_refused_not_paired(tmp_path):
    out = _with_core(tmp_path)
    (legacy,) = out.glob("run-*.json")
    legacy.write_bytes(legacy.read_bytes() + b"\n")  # the legacy record changed after its core record was written
    with pytest.raises(D.ExportError, match="changed after its core record was written"):
        D.export_rows([out])
