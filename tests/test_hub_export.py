"""`smeltery export hf`: a Hugging Face folder (card + croissant.json) from an exported table, and the optional push.

The structural tests always run with pyarrow (the `dataset` extra): Croissant's required @context keys, every Parquet
column a field, every FileObject's sha256 the bytes'. mlcroissant's own validator and `datasets.load_dataset` run when
those packages are installed and skip otherwise. Nothing here touches the network: the push is checked against a fake
`HfApi`, and the folder is written with `datasets` and `huggingface_hub` blocked from importing.
"""

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from smeltery import cli
from smeltery import dataset_export as D
from smeltery import dataset_record as DR
from smeltery import dataset_splits as S
from smeltery import hub_export as H

pytest.importorskip("pyarrow")

PREBUILT = Path(__file__).resolve().parents[1] / "src" / "smeltery" / "prebuilt"
RHF = PREBUILT / "benzoic-rhf" / "run"
STRUCTURE = PREBUILT / "structure" / "run"
DANUGLIPRON = PREBUILT / "danuglipron-halogen" / "run"
TWO = {"train": 0.5, "test": 0.5}
REQUIRED_CONTEXT = ("@vocab", "sc", "cr", "dct", "conformsTo", "recordSet", "field", "fileObject", "source", "extract")


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _tree(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): _sha(p) for p in sorted(root.rglob("*")) if p.is_file()}


def _front_matter(folder: Path) -> str:
    text = (folder / H.CARD).read_text()
    assert text.startswith("---\n")
    return text.split("---\n", 2)[1]


@pytest.fixture(scope="module")
def table(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("table")
    D.write_dataset([RHF, STRUCTURE], d / "runs.parquet")
    return d / "runs.parquet"


@pytest.fixture(scope="module")
def split(table, tmp_path_factory) -> Path:
    p = tmp_path_factory.mktemp("split") / "target-seed0.json"
    S.write_split(S.make_split(table, "target", fractions=TWO), p)
    return p


@pytest.fixture(scope="module")
def folder(table, split, tmp_path_factory) -> Path:
    return H.export_hf(table, tmp_path_factory.mktemp("hf") / "out", split_files=[split])


def _croissant(folder: Path) -> dict:
    return json.loads((folder / H.CROISSANT).read_text())


# ---- structure: always runs ------------------------------------------------------------------------------------------


def test_every_column_has_a_description():
    assert list(H.COLUMN_DESCRIPTIONS) == D.arrow_schema().names


def test_the_folder_holds_the_table_split_partitions_card_and_croissant(folder, table, split):
    assert sorted(_tree(folder)) == [
        "README.md",
        "croissant.json",
        "data/target-seed0/test.parquet",
        "data/target-seed0/train.parquet",
        "dataset/runs.parquet",
        "dataset/splits/target-seed0.json",
    ]
    assert (folder / H.TABLE).read_bytes() == table.read_bytes()  # the table as exported, not rewritten
    assert (folder / "dataset/splits/target-seed0.json").read_bytes() == split.read_bytes()
    assert not list(folder.parent.glob(".*staging*"))


def test_croissant_has_the_required_context_and_type(folder):
    doc = _croissant(folder)
    assert all(k in doc["@context"] for k in REQUIRED_CONTEXT), sorted(set(REQUIRED_CONTEXT) - set(doc["@context"]))
    assert doc["@context"]["cr"] == "http://mlcommons.org/croissant/"
    assert doc["@context"]["@vocab"] == "https://schema.org/"
    assert doc["@type"] == "sc:Dataset" and doc["conformsTo"] == "http://mlcommons.org/croissant/1.0"
    assert doc["name"] and doc["description"]


def test_every_parquet_column_is_a_field_read_from_the_table(folder):
    import pyarrow.parquet as pq

    schema = pq.read_schema(folder / H.TABLE)
    (rs,) = _croissant(folder)["recordSet"]
    fields = {f["name"]: f for f in rs["field"]}
    assert list(fields) == schema.names
    for f in schema:
        if f.name != "scores":
            assert fields[f.name]["source"] == {"fileObject": {"@id": H.TABLE}, "extract": {"column": f.name}}
    scores = fields["scores"]
    assert [s["name"] for s in scores["subField"]] == [c.name for c in schema.field("scores").type.value_type]
    assert all(s["source"]["extract"] == {"column": "scores"} for s in scores["subField"])
    assert {fields[n]["dataType"] for n in ("score", "score_sem")} == {"sc:Float"}
    assert (fields["score_n"]["dataType"], fields["is_parent"]["dataType"]) == ("sc:Integer", "sc:Boolean")


def test_units_are_stated_only_where_one_unit_holds_for_every_row(folder):
    (rs,) = _croissant(folder)["recordSet"]
    with_unit = {f["name"]: f["unitText"] for f in rs["field"] if "unitText" in f}
    rows = D.export_rows([RHF, STRUCTURE])
    assert with_unit == {k: v for k, v in D.column_units(rows).items() if v is not None}
    assert set(with_unit.values()) == {"kcal/mol"}


def test_every_file_object_states_the_sha256_and_size_of_its_bytes(folder):
    doc = _croissant(folder)
    described = {f["contentUrl"] for f in doc["distribution"]}
    assert described == set(_tree(folder)) - {"README.md", "croissant.json"}
    for f in doc["distribution"]:
        p = folder / f["contentUrl"]
        assert f["sha256"] == _sha(p) and f["contentSize"] == f"{p.stat().st_size} B", f["contentUrl"]
    assert H.verify_folder(folder) == []


def test_split_partitions_are_exactly_the_rows_the_split_file_names(folder, split):
    import pyarrow.parquet as pq

    doc = json.loads(split.read_text())
    for part, ids in doc["partitions"].items():
        got = S.identities(pq.read_table(folder / "data" / "target-seed0" / f"{part}.parquet"))
        assert sorted(got) == sorted(tuple(r) for r in ids), part
    full = pq.read_table(folder / H.TABLE)
    for part in doc["partitions"]:
        t = pq.read_table(folder / "data" / "target-seed0" / f"{part}.parquet")
        assert t.schema.equals(full.schema, check_metadata=True)


def test_the_card_front_matter_names_configs_and_features_and_no_invented_license(folder):
    yaml_text = _front_matter(folder)
    assert "license" not in yaml_text
    card = (folder / H.CARD).read_text()
    assert "## License" in card and "**Not stated.**" in card
    assert "MIT OR Apache-2.0" in card  # named only as the CODE license that the data's is not
    assert "license" not in _croissant(folder)
    yaml = pytest.importorskip("yaml")
    front = yaml.safe_load(yaml_text)
    assert [c["config_name"] for c in front["configs"]] == ["default", "target-seed0"]
    assert front["configs"][0]["data_files"] == [{"split": "full", "path": "dataset/runs.parquet"}]
    assert front["configs"][1]["data_files"] == [
        {"split": "train", "path": "data/target-seed0/train.parquet"},
        {"split": "test", "path": "data/target-seed0/test.parquet"},
    ]
    names = [f["name"] for f in front["dataset_info"][0]["features"]]
    assert names == D.arrow_schema().names


def test_the_same_input_writes_the_same_bytes(table, split, folder, tmp_path):
    again = H.export_hf(table, tmp_path / "again", split_files=[split])
    assert _tree(again) == _tree(folder)


def test_writing_needs_neither_datasets_nor_huggingface_hub(table, tmp_path, monkeypatch):
    for mod in ("datasets", "huggingface_hub", "mlcroissant"):
        monkeypatch.setitem(sys.modules, mod, None)  # importing it now raises ImportError
    out = H.export_hf(table, tmp_path / "out", license="CC0-1.0")
    assert H.verify_folder(out) == []


def test_importing_the_module_imports_no_hub_client():
    code = (
        "import sys, smeltery.hub_export; assert 'huggingface_hub' not in sys.modules and 'datasets' not in sys.modules"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_an_existing_folder_an_arrow_table_and_a_foreign_split_are_refused(table, folder, tmp_path):
    with pytest.raises(H.HubError, match="not empty"):
        H.export_hf(table, folder)
    D.write_dataset([RHF], tmp_path / "t.arrow", "arrow")
    with pytest.raises(H.HubError, match="Parquet"):
        H.export_hf(tmp_path / "t.arrow", tmp_path / "a")
    D.write_dataset([STRUCTURE, RHF], tmp_path / "other.parquet")
    other = tmp_path / "s.json"
    S.write_split(S.make_split(tmp_path / "other.parquet", "target", fractions=TWO), other)
    with pytest.raises(S.SplitError, match="table changed"):
        H.export_hf(table, tmp_path / "b", split_files=[other])
    assert not (tmp_path / "a").exists() and not (tmp_path / "b").exists()


# ---- licence ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("spdx", "front", "croissant"),
    [
        ("CC-BY-4.0", {"license": "cc-by-4.0"}, "https://spdx.org/licenses/CC-BY-4.0.html"),
        ("CC0-1.0", {"license": "cc0-1.0"}, "https://spdx.org/licenses/CC0-1.0.html"),
        ("MIT OR Apache-2.0", {"license": "other", "license_name": "mit-or-apache-2.0"}, "MIT OR Apache-2.0"),
        ("LicenseRef-acme", {"license": "other", "license_name": "licenseref-acme"}, "LicenseRef-acme"),
    ],
)
def test_a_stated_license_maps_to_the_hub_id_or_other(spdx, front, croissant):
    assert H.hf_license(spdx) == front
    assert H.croissant_license(spdx) == croissant


def test_a_stated_license_reaches_card_and_croissant(table, tmp_path):
    out = H.export_hf(table, tmp_path / "out", license="CC-BY-4.0")
    assert 'license: "cc-by-4.0"' in _front_matter(out)
    assert _croissant(out)["license"] == "https://spdx.org/licenses/CC-BY-4.0.html"
    assert "`CC-BY-4.0` (SPDX), as given to `smeltery export hf --license`" in (out / H.CARD).read_text()


@pytest.mark.parametrize("bad", ["NOASSERTION", "NONE", "MIT; rm -rf", ""])
def test_a_license_that_is_not_spdx_is_refused_before_writing(table, tmp_path, bad):
    with pytest.raises(H.HubError, match="SPDX"):
        H.export_hf(table, tmp_path / "out", license=bad)
    assert not (tmp_path / "out").exists()


# ---- provenance ------------------------------------------------------------------------------------------------------


def test_provenance_names_each_run_and_the_ferric_builds_its_rows_state(folder):
    rows = D.export_rows([RHF, STRUCTURE])
    derived = _croissant(folder)["prov:wasDerivedFrom"]
    assert [e["identifier"] for e in derived] == list(dict.fromkeys(r["run_id"] for r in rows))
    for e in derived:
        mine = [r for r in rows if r["run_id"] == e["identifier"]]
        shas = sorted({r["ferric_git_sha"] for r in mine})
        agents = e["prov:wasGeneratedBy"]["prov:wasAssociatedWith"]
        assert [a["identifier"] for a in agents] == shas and all(a["name"] == "ferric" for a in agents)
        assert "sha256" not in e  # the prebuilt runs carry no core record: no digest is invented
    card = (folder / H.CARD).read_text()
    assert all(f"`{e['identifier']}`" in card for e in derived)


def test_a_mixed_build_run_counts_rows_whose_build_is_not_recorded(tmp_path):
    D.write_dataset([DANUGLIPRON], tmp_path / "t.parquet")
    out = H.export_hf(tmp_path / "t.parquet", tmp_path / "out")
    (e,) = _croissant(out)["prov:wasDerivedFrom"]
    activity = e["prov:wasGeneratedBy"]
    assert activity["prov:wasAssociatedWith"] == []  # no build is claimed for any row
    rows = D.export_rows([DANUGLIPRON])
    assert f"unrecorded {len(rows)}" in activity["description"]
    assert "unrecorded (13)" in (out / H.CARD).read_text()


# ---- a dataset directory (attestation-schema with attestation.dataset/1) ---------------------------------------------


@pytest.fixture(scope="module")
def dataset_dir(tmp_path_factory):
    if not DR.AVAILABLE:
        pytest.skip(DR.not_installed())
    from smeltery import core_record

    root = tmp_path_factory.mktemp("runs")
    runs = []
    for src in (RHF, STRUCTURE):
        d = root / src.parent.name
        shutil.copytree(src, d)
        assert core_record.main(["core", str(d)]) == 0
        runs.append(d)
    dest = tmp_path_factory.mktemp("ds") / "mwe" / "1"
    DR.export_dataset_dir(
        runs, dest, dataset_id="mwe", version="1", license="CC-BY-4.0", splits=[DR.SplitSpec("target", 0, TWO)]
    )
    return dest


def test_a_dataset_directory_keeps_its_record_and_its_digests_are_reused(dataset_dir, tmp_path):
    out = H.export_hf(dataset_dir, tmp_path / "out")
    record = json.loads((dataset_dir / "attestation" / "dataset.json").read_text())
    doc = _croissant(out)
    by_path = {f["contentUrl"]: f["sha256"] for f in doc["distribution"]}
    for f in record["files"]:
        assert by_path[f"dataset/{f['path']}"] == f["sha256"]  # the record's digest, carried over
    digests = by_path["dataset/attestation/digests.json"]
    assert digests == record["content_sha256"] == doc["identifier"].removeprefix("sha256:")
    assert _tree(out / "dataset") == _tree(dataset_dir)  # the dataset directory, byte for byte
    # and still a conformant one inside the Hub folder
    from attestation_schema import conformance

    report = conformance.check_dataset_dir(out / "dataset")
    assert report.ok, report.format()
    # provenance: the core records' digests the record names, by run
    shas = {e["identifier"]: e.get("sha256") for e in doc["prov:wasDerivedFrom"]}
    assert shas == {s["run_id"]: s["record_sha256"] for s in record["source_runs"]}
    assert all(shas.values())
    # the record's license is the data's, and a different --license is refused
    assert doc["license"] == "https://spdx.org/licenses/CC-BY-4.0.html" and doc["version"] == "1"
    assert "as the dataset record `dataset/attestation/dataset.json` states it" in (out / H.CARD).read_text()
    with pytest.raises(H.HubError, match="disagrees"):
        H.export_hf(dataset_dir, tmp_path / "other", license="CC0-1.0")


def test_editing_the_hub_folder_in_place_leaves_the_source_dataset_intact(dataset_dir, tmp_path):
    from attestation_schema import conformance

    before = _tree(dataset_dir)
    out = H.export_hf(dataset_dir, tmp_path / "out")
    for rel in before:
        a, b = (out / "dataset" / rel).stat(), (dataset_dir / rel).stat()
        assert (a.st_dev, a.st_ino) != (b.st_dev, b.st_ino), rel  # a copy, not a hard link
    for rel in before:  # write THROUGH each file, as an editor saving in place would
        with open(out / "dataset" / rel, "r+b") as f:
            f.seek(0)
            f.write(b"X")
    assert _tree(dataset_dir) == before
    assert conformance.check_dataset_dir(dataset_dir).ok
    assert any("sha256" in m for m in H.verify_folder(out))  # the Hub folder itself notices


def test_a_bare_table_is_copied_not_linked(table, folder):
    a, b = (folder / H.TABLE).stat(), table.stat()
    assert (a.st_dev, a.st_ino) != (b.st_dev, b.st_ino)


def test_a_tampered_dataset_directory_is_refused(dataset_dir, tmp_path):
    bad = tmp_path / "bad"
    shutil.copytree(dataset_dir, bad)
    p = bad / "splits" / "target-seed0.json"
    p.write_text(p.read_text().replace('"seed": 0', '"seed": 1'))
    with pytest.raises(D.ExportError, match="target-seed0.json"):
        H.export_hf(bad, tmp_path / "out")
    assert not (tmp_path / "out").exists()


# ---- push (no network: a fake client) --------------------------------------------------------------------------------


class FakeApi:
    def __init__(self):
        self.calls = []

    def create_repo(self, repo_id, **kw):
        self.calls.append(("create_repo", repo_id, kw))

    def upload_folder(self, **kw):
        self.calls.append(("upload_folder", kw["repo_id"], kw))


@pytest.fixture
def pushable(table, tmp_path):
    return H.export_hf(table, tmp_path / "out", license="CC0-1.0", repo_id="owner/smeltery-mwe")


def test_the_dry_run_lists_every_file_and_the_repository(pushable, capsys):
    plan = H.plan_push(pushable, "owner/smeltery-mwe")
    assert [rel for rel, _ in plan.files] == sorted(_tree(pushable))
    assert plan.url == "https://huggingface.co/datasets/owner/smeltery-mwe"
    assert _croissant(pushable)["url"] == plan.url
    assert 'load_dataset("owner/smeltery-mwe", "default"' in (pushable / H.CARD).read_text()


def test_push_creates_a_dataset_repo_and_uploads_the_folder_with_the_clients_own_token(pushable):
    api = FakeApi()
    H.push(pushable, "owner/smeltery-mwe", api=api)
    (create, upload) = api.calls
    assert create[:2] == ("create_repo", "owner/smeltery-mwe") and create[2]["repo_type"] == "dataset"
    assert create[2]["exist_ok"] is True and create[2]["private"] is None
    assert upload[2]["folder_path"] == str(pushable) and upload[2]["repo_type"] == "dataset"
    assert all("token" not in kw for _, _, kw in api.calls)  # huggingface_hub resolves HF_TOKEN / the cached login


@pytest.mark.parametrize("repo", ["smeltery-mwe", "", "owner/", "/name", "a/b/c", "owner/na me"])
def test_a_repository_id_that_is_not_owner_slash_name_is_refused(pushable, repo):
    with pytest.raises(H.HubError, match="OWNER/NAME"):
        H.push(pushable, repo, api=FakeApi())


def test_push_refuses_a_folder_that_changed_states_no_license_or_was_made_for_another_repo(table, pushable, tmp_path):
    api = FakeApi()
    with pytest.raises(H.HubError, match="was generated for"):
        H.push(pushable, "owner/another", api=api)
    p = pushable / H.TABLE
    data = p.read_bytes()
    p.write_bytes(data + b"\0")
    with pytest.raises(H.HubError, match="dataset/runs.parquet: sha256"):
        H.push(pushable, "owner/smeltery-mwe", api=api)
    (pushable / "extra.txt").write_text("x")
    assert any("extra.txt" in m for m in H.verify_folder(pushable))
    unlicensed = H.export_hf(table, tmp_path / "unlicensed", repo_id="owner/x")
    with pytest.raises(H.HubError, match="license is not stated"):
        H.push(unlicensed, "owner/x", api=api)
    assert api.calls == []


def test_the_cli_is_a_dry_run_unless_confirmed_and_names_the_extra_without_the_client(
    table, tmp_path, monkeypatch, capsys
):
    out = tmp_path / "out"
    args = ["export", "hf", str(table), "-o", str(out), "--license", "CC0-1.0", "--push", "owner/x"]
    assert cli.main(args) == 0
    printed = capsys.readouterr().out
    assert "would upload" in printed and "dry run: nothing uploaded" in printed
    monkeypatch.setitem(sys.modules, "huggingface_hub", None)
    assert cli.main(["export", "hf", "push", str(out), "owner/x", "--confirm"]) == 2
    assert "smeltery[hub]" in capsys.readouterr().err
    # --push without a license refuses before anything is written
    assert cli.main(["export", "hf", str(table), "-o", str(tmp_path / "n"), "--push", "owner/x"]) == 2
    assert "license is not stated" in capsys.readouterr().err and not (tmp_path / "n").exists()


# ---- third-party validators, when installed --------------------------------------------------------------------------


def test_mlcroissant_validates_the_json_ld_and_reads_every_row(folder):
    mlc = pytest.importorskip("mlcroissant")
    ds = mlc.Dataset(str(folder / H.CROISSANT))
    assert not ds.metadata.ctx.issues.errors, ds.metadata.ctx.issues.errors
    import pyarrow.parquet as pq

    records = list(ds.records(H.RECORD_SET))
    assert len(records) == pq.read_metadata(folder / H.TABLE).num_rows
    assert records[0]["runs/scores"][0]["runs/scores/unit"] in (b"kcal/mol", "kcal/mol")


def test_datasets_loads_every_config_the_card_declares(folder, tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("HF_DATASETS_OFFLINE", "1")
    datasets = pytest.importorskip("datasets")
    import pyarrow.parquet as pq

    full = datasets.load_dataset(str(folder), "default", split="full", cache_dir=str(tmp_path / "cache"))
    table = pq.read_table(folder / H.TABLE)
    assert full.num_rows == table.num_rows
    assert full.features == datasets.Features.from_arrow_schema(table.schema)
    assert H.hf_features(H._schema_fields(table.schema)) == full.features._to_yaml_list()
    parts = datasets.load_dataset(str(folder), "target-seed0", cache_dir=str(tmp_path / "cache"))
    assert sorted(parts) == ["test", "train"] and sum(p.num_rows for p in parts.values()) == table.num_rows
    plain = datasets.load_dataset("parquet", data_files={"full": str(folder / H.TABLE)}, cache_dir=str(tmp_path / "c2"))
    assert plain["full"].num_rows == table.num_rows


def test_only_the_upload_imports_the_hub_client_and_nothing_imports_attestation():
    import ast

    tree = ast.parse(Path(H.__file__).read_text())
    where = {}
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef):
            for node in ast.walk(fn):
                if isinstance(node, ast.Import | ast.ImportFrom):
                    for name in [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]:
                        where.setdefault(name.split(".")[0], set()).add(fn.name)
    top = {
        (a.name if isinstance(n, ast.Import) else n.module or "").split(".")[0]
        for n in tree.body
        if isinstance(n, ast.Import | ast.ImportFrom)
        for a in n.names
    }
    assert where.get("huggingface_hub") == {"push"} and "huggingface_hub" not in top
    assert "attestation" not in top | set(where) and "datasets" not in top | set(where)
