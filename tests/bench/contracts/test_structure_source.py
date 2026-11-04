import pytest

from bench.contracts.base import PortContract
from smeltery.bench.adapters.structure_source import LocalStructureSource
from smeltery.bench.ports import StructureSource


class StructureSourceContract(PortContract):
    port = StructureSource

    @classmethod
    def adapters(cls):
        return [("LocalStructureSource", lambda tmp: LocalStructureSource(root=tmp / "structs", allow_network=False))]


@pytest.fixture(params=StructureSourceContract.adapters(), ids=[n for n, _ in StructureSourceContract.adapters()])
def src(request, bench_tmp):
    return request.param[1](bench_tmp)


@pytest.fixture
def pdb_file(bench_tmp):
    p = bench_tmp / "x.pdb"
    p.write_text("ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00  0.00           N\n")
    return p


def test_a_local_pdb_path_resolves(src, pdb_file):
    assert src.fetch(str(pdb_file)).exists()


def test_a_pdb_id_without_network_permission_is_refused(src):
    with pytest.raises(Exception) as e:
        src.fetch("1ABC")
    assert "network" in str(e.value).lower()
