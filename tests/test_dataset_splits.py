"""`smeltery.dataset_splits` and `smeltery.dataset_torch`, on the committed prebuilt runs exported by dataset_export.

The split computation needs pyarrow (and RDKit for scaffolds) but never torch; the tensor tests importorskip torch."""

import json
import sys
from pathlib import Path

import pytest

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")

from smeltery import dataset_export as D  # noqa: E402
from smeltery import dataset_splits as S  # noqa: E402
from smeltery.dataset_torch import ExportDataset  # noqa: E402

PREBUILT = Path(__file__).resolve().parents[1] / "src" / "smeltery" / "prebuilt"
ALL_RUNS = [PREBUILT / n / "run" for n in ("benzoic-rhf", "benzoic-pbe", "docking", "structure", "danuglipron-halogen")]
FR = {"train": 0.5, "valid": 0.25, "test": 0.25}


@pytest.fixture(scope="module")
def table(tmp_path_factory):
    p = tmp_path_factory.mktemp("tbl") / "all.parquet"
    D.write_dataset(ALL_RUNS, p)
    return p


def ident(r):
    return tuple(r)


def test_scaffold_split_keeps_every_scaffold_in_one_partition_and_covers_every_row_once(table):
    Chem = pytest.importorskip("rdkit.Chem")
    from rdkit.Chem.Scaffolds import MurckoScaffold

    split = S.make_split(table, "scaffold", seed=3, fractions=FR)
    t = S.read_table(table)
    smi = {S.identities(t)[i]: s for i, s in enumerate(t.column("smiles").to_pylist())}
    owner: dict[str, set[str]] = {}
    seen = []
    for name, rows in split["partitions"].items():
        assert rows, name
        for r in rows:
            seen.append(ident(r))
            scaf = Chem.MolToSmiles(MurckoScaffold.GetScaffoldForMol(Chem.MolFromSmiles(smi[ident(r)])))
            owner.setdefault(scaf, set()).add(name)
    assert len(seen) == len(set(seen)) == t.num_rows
    assert set(seen) == set(S.identities(t))
    assert len(owner) >= 3 and all(len(v) == 1 for v in owner.values()), owner
    assert split["table"]["sha256"] == S.table_sha256(table)
    assert (split["kind"], split["seed"], split["algorithm_version"]) == ("scaffold", 3, 1)


def test_target_split_keeps_every_target_in_one_partition(table):
    split = S.make_split(table, "target", seed=1, fractions=FR)
    owner: dict[str, set[str]] = {}
    for name, rows in split["partitions"].items():
        for r in rows:
            owner.setdefault(r[2], set()).add(name)
    assert len(owner) == 3 and all(len(v) == 1 for v in owner.values())
    assert {next(iter(v)) for v in owner.values()} == set(FR)


def test_same_table_and_seed_give_a_byte_identical_file_and_the_seed_matters(table, tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    S.write_split(S.make_split(table, "scaffold", seed=7, fractions=FR), a)
    S.write_split(S.make_split(table, "scaffold", seed=7, fractions=FR), b)
    assert a.read_bytes() == b.read_bytes()
    docs = {S.dumps(S.make_split(table, "scaffold", seed=s, fractions=FR)) for s in range(12)}
    assert len(docs) > 1, "the seed changes nothing"
    # re-exporting the same runs gives the same rows; the split still loads against its own table only
    assert json.loads(a.read_text())["table"]["sha256"] == S.table_sha256(table)


def test_a_table_with_a_different_digest_is_refused_loudly(table, tmp_path):
    sp = tmp_path / "s.json"
    S.write_split(S.make_split(table, "target", seed=0, fractions=FR), sp)
    S.load_split(sp, table)  # the table it was made on
    t = pq.read_table(table)
    i = t.column_names.index("score")
    vals = t.column("score").to_pylist()
    vals[0] = 12345.0 if vals[0] != 12345.0 else 1.0
    changed = tmp_path / "changed.parquet"
    pq.write_table(t.set_column(i, "score", pa.array(vals, type=t.schema.field("score").type)), changed)
    with pytest.raises(S.SplitError, match="table changed"):
        S.load_split(sp, changed)
    with pytest.raises(S.SplitError, match="table changed"):
        ExportDataset(changed, features=["score_n"], target="score", split=sp, partition="train", tensors=False)
    arrow = tmp_path / "all.arrow"
    D.write_dataset(ALL_RUNS, arrow, "arrow")  # same rows, a different file: a different digest
    with pytest.raises(S.SplitError, match="table changed"):
        S.load_split(sp, arrow)


def test_a_hand_edited_split_is_refused(table, tmp_path):
    doc = S.make_split(table, "target", seed=0, fractions=FR)
    moved = doc["partitions"]["train"].pop()
    doc["partitions"]["test"].append(moved)
    sp = tmp_path / "s.json"
    S.write_split(doc, sp)
    S.load_split(sp, table)  # still a partition of the table: edits that keep coverage are the digest's blind spot
    doc["partitions"]["valid"].append(moved)
    S.write_split(doc, sp)
    with pytest.raises(S.SplitError, match="partitions"):
        S.load_split(sp, table)
    doc["partitions"]["valid"].remove(moved)
    doc["partitions"]["test"].remove(moved)
    S.write_split(doc, sp)
    with pytest.raises(S.SplitError, match="edited"):
        S.load_split(sp, table)
    for bad in ({"format": "x"}, {**doc, "schema_version": 99}, {**doc, "algorithm_version": 99}):
        sp.write_text(json.dumps(bad))
        with pytest.raises(S.SplitError):
            S.load_split(sp, table)


def test_scaffold_split_refuses_without_rdkit_rather_than_falling_back(table, monkeypatch):
    monkeypatch.setitem(sys.modules, "rdkit", None)
    with pytest.raises(S.SplitError, match="RDKit"):
        S.make_split(table, "scaffold", fractions=FR)
    S.make_split(table, "target", fractions=FR)  # target needs no RDKit


def test_an_unparseable_smiles_is_refused(table, tmp_path):
    t = pq.read_table(table)
    smi = t.column("smiles").to_pylist()
    smi[0] = "not a smiles ((("
    bad = tmp_path / "bad.parquet"
    pq.write_table(t.set_column(t.column_names.index("smiles"), "smiles", pa.array(smi)), bad)
    with pytest.raises(S.SplitError, match="cannot parse"):
        S.make_split(bad, "scaffold", fractions=FR)


def test_assign_groups_is_pure_and_refuses_too_few_groups():
    sizes = {"a": 10, "b": 5, "c": 5, "d": 1}
    one = S.assign_groups(sizes, {"train": 0.6, "test": 0.4}, 0)
    assert one == S.assign_groups(sizes, {"train": 0.6, "test": 0.4}, 0)
    assert set(one.values()) == {"train", "test"} and set(one) == set(sizes)
    with pytest.raises(S.SplitError, match="cannot fill"):
        S.assign_groups({"a": 1}, {"train": 0.5, "test": 0.5}, 0)
    for bad in ({"train": 1.0}, {"train": 0.5, "test": 0.6}, {"train": 1.5, "test": -0.5}):
        with pytest.raises(S.SplitError):
            S._fractions(bad)


def test_cli_writes_and_verifies_through_the_export_command(table, tmp_path, capsys):
    out = tmp_path / "s.json"
    assert (
        D.main(["split", "target", str(table), "-o", str(out), "--seed", "2", "--fractions", "train=0.5,test=0.5"]) == 0
    )
    assert json.loads(out.read_text())["parameters"]["fractions"] == {"train": 0.5, "test": 0.5}
    assert D.main(["split", "verify", str(out), str(table)]) == 0
    assert D.main(["split", "scaffold", str(tmp_path / "missing.parquet"), "-o", str(tmp_path / "x.json")]) == 2
    assert "smeltery export split" in capsys.readouterr().err


def test_dataset_without_torch_filters_by_split_tier_and_decision(table, tmp_path):
    sp = tmp_path / "s.json"
    S.write_split(S.make_split(table, "target", seed=0, fractions=FR), sp)
    t = pq.read_table(table).to_pylist()
    seen = 0
    for part in FR:
        ds = ExportDataset(
            table, features=["score_n", "score_sem"], target="score", split=sp, partition=part, tensors=False
        )
        want = [
            r
            for r in t
            if [r["run_id"], r["ligand"], r["target"], r["tier"]] in json.loads(sp.read_text())["partitions"][part]
        ]
        want = [r for r in want if r["score"] is not None]
        assert len(ds) == len(want) and ds.n_null_target == 0
        seen += len(ds)
        x, y = ds[0]
        r0 = [r for r in want if (r["run_id"], r["ligand"], r["target"], r["tier"]) == ds.identity(0)][0]
        assert y == r0["score"] and x[0] == r0["score_n"]
    assert seen == len(t)
    ds = ExportDataset(table, features=["score_n"], target="score", tiers=["docking"], tensors=False)
    assert len(ds) == sum(r["tier"] == "docking" for r in t) > 0
    ds = ExportDataset(table, features=["score_n"], target="score", decisions=["pass"], tensors=False)
    assert len(ds) == sum(r["decision"] == "pass" for r in t) > 0
    ds = ExportDataset(table, features=["score_floor"], target="score", transform=lambda s: s[0], tensors=False)
    assert len(ds[0]) == 1 and any(v != v for i in range(len(ds)) for v in ds[i])  # a null sem is NaN, not dropped
    assert len(ds) == len(t)


def test_dataset_refuses_bad_columns_and_half_a_split(table, tmp_path):
    with pytest.raises(ValueError, match="no column"):
        ExportDataset(table, features=["nope"], target="score", tensors=False)
    with pytest.raises(ValueError, match="not numeric"):
        ExportDataset(table, features=["smiles"], target="score", tensors=False)
    with pytest.raises(ValueError, match="both"):
        ExportDataset(table, features=["score_n"], target="score", partition="train", tensors=False)
    with pytest.raises(ValueError, match="unknown decision"):
        ExportDataset(table, features=["score_n"], target="score", decisions=["maybe"], tensors=False)
    sp = tmp_path / "s.json"
    S.write_split(S.make_split(table, "target", fractions=FR), sp)
    with pytest.raises(S.SplitError, match="no partition"):
        ExportDataset(table, features=["score_n"], target="score", split=sp, partition="dev", tensors=False)


def test_null_targets_are_dropped_and_counted(table):
    ds = ExportDataset(table, features=["score_n"], target="paired_ddE", tensors=False)
    t = pq.read_table(table).to_pylist()
    n_null = sum(r["paired_ddE"] is None for r in t)
    assert n_null and ds.n_null_target == n_null and len(ds) == len(t) - n_null


def test_tensor_dataset_feeds_a_dataloader(table, tmp_path):
    torch = pytest.importorskip("torch")
    sp = tmp_path / "s.json"
    S.write_split(S.make_split(table, "scaffold", seed=0, fractions=FR), sp)
    ds = ExportDataset(table, features=["score_n", "score_sem"], target="score", split=sp, partition="train")
    x, y = ds[0]
    assert x.dtype == y.dtype == torch.float32 and x.shape == (2,) and y.shape == ()
    xb, yb = next(iter(torch.utils.data.DataLoader(ds, batch_size=len(ds))))
    assert xb.shape == (len(ds), 2) and yb.shape == (len(ds),)
    ds2 = ExportDataset(table, features=["score_n"], target="score", transform=lambda s: (s[0] * 2, s[1]))
    assert float(ds2[0][0][0]) == 2 * float(ExportDataset(table, features=["score_n"], target="score")[0][0][0])


def test_tensors_without_torch_say_how_to_get_it(table, monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", None)
    with pytest.raises(ImportError, match=r"smeltery\[torch\]"):
        ExportDataset(table, features=["score_n"], target="score")
