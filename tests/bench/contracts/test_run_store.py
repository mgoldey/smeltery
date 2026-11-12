import pytest
from gui_fixture import make_run_dir

from bench.contracts.base import PortContract
from smeltery.bench.adapters.run_store import FilesystemRunStore
from smeltery.bench.ports import RunStore


class RunStoreContract(PortContract):
    port = RunStore

    @classmethod
    def adapters(cls):
        return [("FilesystemRunStore", lambda tmp: FilesystemRunStore(tmp / "root"))]


@pytest.fixture(params=RunStoreContract.adapters(), ids=[n for n, _ in RunStoreContract.adapters()])
def store(request, bench_tmp):
    return request.param[1](bench_tmp)


@pytest.fixture
def store_factory(bench_tmp):
    def factory(root):
        return FilesystemRunStore(root)

    return factory


@pytest.fixture
def run_dir(bench_tmp):
    return make_run_dir(bench_tmp / "root")


def test_lists_a_contract_shaped_run(store, run_dir):
    rows = store.list_runs()
    assert len(rows) == 1 and "error" not in rows[0]
    assert rows[0]["parent"] == "benzoic"
    assert rows[0]["keep"] == 1


def test_a_malformed_record_is_a_row_carrying_error(store, run_dir):
    (run_dir.parent / "run-deadbeef0000.json").write_text("{not json")
    rows = store.list_runs()
    assert len(rows) == 2
    assert sum("error" in r for r in rows) == 1
    assert all(r.get("error") for r in rows if "error" in r)


def test_one_bad_record_does_not_hide_the_good_ones(store, run_dir):
    (run_dir.parent / "run-000000000bad.json").write_text(")")
    assert sum("error" not in r for r in store.list_runs()) == 1


def test_missing_root_lists_nothing_rather_than_raising(store_factory, bench_tmp):
    assert store_factory(bench_tmp / "absent").list_runs() == []


def test_read_run_returns_a_Run_whose_candidates_match(store, run_dir):
    run = store.read_run(next(iter(store.find_runs())))
    assert sorted(run.candidates) == ["4-Br", "4-Cl", "4-F", "benzoic"]


def test_every_row_carries_a_stable_id(store, run_dir):
    (row,) = store.list_runs()
    assert row["id"] and not row["id"].isdigit()
    assert row["id"] == store.list_runs()[0]["id"], "listing twice gives the same id"


def test_adapter_satisfies_the_protocol(store):
    assert isinstance(store, RunStore)
