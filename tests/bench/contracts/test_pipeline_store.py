import hashlib

import pytest

from bench.contracts.base import PortContract
from smeltery.bench.adapters.pipeline_store import FilesystemPipelineStore
from smeltery.bench.ports import PipelineStore, StaleWrite


class PipelineStoreContract(PortContract):
    port = PipelineStore

    @classmethod
    def adapters(cls):
        return [("FilesystemPipelineStore", lambda tmp: FilesystemPipelineStore(tmp))]


@pytest.fixture(params=PipelineStoreContract.adapters(), ids=[n for n, _ in PipelineStoreContract.adapters()])
def store(request, bench_tmp):
    return request.param[1](bench_tmp)


def test_it_satisfies_the_port(store):
    assert isinstance(store, PipelineStore)


def test_create_then_get_round_trips_text_and_hash(store):
    made = store.create("cdk2", 'campaign = "x"\n', {})
    got = store.get("cdk2")
    assert got.text == 'campaign = "x"\n' == made.text
    assert got.sha256 == hashlib.sha256(b'campaign = "x"\n').hexdigest()
    assert (got.folder / "config.toml").read_text() == got.text
    assert store.list() == ["cdk2"]


def test_put_with_the_current_hash_replaces_the_text(store):
    a = store.create("p", "a = 1\n", {})
    b = store.put("p", "a = 2\n", a.sha256)
    assert b.sha256 != a.sha256 and store.get("p").text == "a = 2\n"


def test_a_stale_write_is_refused_and_changes_nothing(store):
    a = store.create("p", "a = 1\n", {})
    store.put("p", "a = 2\n", a.sha256)
    with pytest.raises(StaleWrite):
        store.put("p", "a = 3\n", a.sha256)
    with pytest.raises(StaleWrite):
        store.put("p", "a = 3\n", None)
    assert store.get("p").text == "a = 2\n"


def test_names_are_validated(store):
    for bad in ("", "A", "-x", "a/b", "..", "a b", "x" * 65, "a.b"):
        with pytest.raises(ValueError):
            store.create(bad, "", {})
        with pytest.raises(ValueError):
            store.get(bad)
    store.create("a0_-z", "", {})
    store.create("x" * 64, "", {})


def test_an_unknown_pipeline_is_a_key_error_and_a_taken_name_is_refused(store):
    with pytest.raises(KeyError):
        store.get("nope")
    with pytest.raises(KeyError):
        store.put("nope", "", None)
    store.create("p", "", {})
    with pytest.raises(FileExistsError):
        store.create("p", "other", {})
    assert store.get("p").text == ""


def test_create_copies_input_files_at_their_relative_paths(store, tmp_path):
    src = tmp_path / "elsewhere.pdb"
    src.write_text("ATOM\n")
    got = store.create("p", "", {"inputs/abc.pdb": src})
    assert (got.folder / "inputs" / "abc.pdb").read_text() == "ATOM\n"


def test_create_refuses_a_path_that_leaves_the_folder_and_leaves_nothing_behind(store, tmp_path):
    src = tmp_path / "f"
    src.write_text("x")
    for rel in ("../escape", "/abs", "config.toml"):
        with pytest.raises(ValueError):
            store.create("p", "", {rel: src})
    assert store.list() == []
    store.create("p", "", {})  # the name was not consumed by the failures
