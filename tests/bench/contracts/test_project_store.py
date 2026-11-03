import pytest

from bench.contracts.base import PortContract
from smeltery.bench.adapters.project_store import SqliteProjectStore
from smeltery.bench.ports import ProjectStore


class ProjectStoreContract(PortContract):
    port = ProjectStore

    @classmethod
    def adapters(cls):
        return [("SqliteProjectStore", lambda tmp: SqliteProjectStore(tmp / "projects.sqlite"))]


@pytest.fixture(params=ProjectStoreContract.adapters(), ids=[n for n, _ in ProjectStoreContract.adapters()])
def pstore(request, bench_tmp):
    return request.param[1](bench_tmp)


def test_an_entry_is_append_only(pstore):
    c = pstore.create_campaign(name="cdk2")
    e = pstore.add_entry(c.id, title="first", body="b", author="mg")
    with pytest.raises(ValueError):
        if hasattr(pstore, "edit_entry"):
            pstore.edit_entry(e.id, body="changed")
        else:
            raise ValueError("no edit")


def test_a_correction_is_a_new_entry_that_supersedes(pstore):
    c = pstore.create_campaign(name="cdk2")
    e1 = pstore.add_entry(c.id, title="first", body="b", author="mg")
    e2 = pstore.add_entry(c.id, title="correction", body="b2", author="mg", supersedes=e1.id)
    assert [x.id for x in pstore.entries(c.id)] == [e1.id, e2.id]
    assert e2.supersedes == e1.id
