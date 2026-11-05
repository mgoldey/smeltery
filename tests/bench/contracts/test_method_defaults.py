import tomllib

import pytest

from bench.contracts.base import PortContract
from smeltery.bench.adapters.method_defaults import FilesystemMethodDefaults
from smeltery.bench.ports.method_defaults import MethodDefaults


class MethodDefaultsContract(PortContract):
    port = MethodDefaults

    @classmethod
    def adapters(cls):
        return [("FilesystemMethodDefaults", lambda tmp: FilesystemMethodDefaults(tmp / "methods.toml"))]


@pytest.fixture(params=MethodDefaultsContract.adapters(), ids=[n for n, _ in MethodDefaultsContract.adapters()])
def store(request, bench_tmp):
    return request.param[1](bench_tmp)


def test_it_satisfies_the_port(store):
    assert isinstance(store, MethodDefaults)


def test_nothing_is_set_until_something_is(store):
    assert store.all() == {}
    assert store.get("cut") == {}


def test_put_then_get_round_trips_and_all_lists_it(store):
    store.put("field_dft_interaction", {"basis": "def2-svp", "memory_budget_gb": 16.0})
    assert store.get("field_dft_interaction") == {"basis": "def2-svp", "memory_budget_gb": 16.0}
    assert store.all() == {"field_dft_interaction": {"basis": "def2-svp", "memory_budget_gb": 16.0}}


def test_put_replaces_the_methods_table_and_an_empty_one_clears_it(store):
    store.put("cut", {"z": 3.0})
    store.put("cut", {"floor": 1.5})
    assert store.get("cut") == {"floor": 1.5}
    store.put("cut", {})
    assert store.all() == {}


def test_a_value_keeps_its_type(store):
    store.put("m", {"a": 1, "b": 1.0, "c": True, "d": [1.0, 2.0, 3.0]})
    got = store.get("m")
    assert [type(got[k]) for k in "abc"] == [int, float, bool] and got["d"] == [1.0, 2.0, 3.0]


def test_the_file_keeps_its_comments_across_edits(store, bench_tmp):
    path = bench_tmp / "methods.toml"
    store.put("cut", {"z": 3.0})
    path.write_text("# my lab's choices\n" + path.read_text())
    store.put("cut", {"z": 2.5})
    store.put("gfn2", {})
    text = path.read_text()
    assert text.startswith("# my lab's choices\n") and tomllib.loads(text) == {"cut": {"z": 2.5}}


def test_a_hand_broken_file_is_a_config_text_error_naming_the_file_and_line(store, bench_tmp):
    from smeltery.gui.config_text import ConfigTextError

    path = bench_tmp / "methods.toml"
    path.write_text("[cut]\nz = 2.0\nbad = = 1\n")
    for call in (store.all, lambda: store.get("cut"), lambda: store.put("cut", {"z": 3.0})):
        with pytest.raises(ConfigTextError) as e:
            call()
        assert e.value.line == 3 and str(path) in str(e.value)
    assert path.read_text() == "[cut]\nz = 2.0\nbad = = 1\n"
