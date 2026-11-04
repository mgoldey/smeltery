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


def test_a_proposal_round_trips_and_filters_by_run(pstore):
    from smeltery.project import Proposal

    a = Proposal(
        id="p1", run="r1", smiles="Cc1ccccc1", reason="synthesis", author="mg", created_at="2026-11-04T00:00:00Z"
    )
    b = Proposal(id="p2", run="r2", smiles="Oc1ccccc1", reason="analog", author="mg", created_at="2026-11-04T00:00:01Z")
    pstore.add_proposal(a)
    pstore.add_proposal(b)
    assert [p.id for p in pstore.list_proposals()] == ["p1", "p2"]
    got = pstore.list_proposals("r1")
    assert [(p.id, p.smiles, p.reason, p.author) for p in got] == [("p1", "Cc1ccccc1", "synthesis", "mg")]


def test_an_invalid_proposal_is_refused_and_not_stored(pstore):
    import pytest

    from smeltery.project import Proposal

    bad = Proposal(id="p3", run="r1", smiles="not a smiles", reason="synthesis", created_at="2026-11-04T00:00:00Z")
    with pytest.raises(ValueError):
        pstore.add_proposal(bad)
    assert pstore.list_proposals() == []
