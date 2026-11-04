import pytest

from bench.contracts.base import PortContract
from smeltery.bench.adapters.comment_store import SqliteCommentStore
from smeltery.bench.ports import CommentStore


class CommentStoreContract(PortContract):
    port = CommentStore

    @classmethod
    def adapters(cls):
        return [("SqliteCommentStore", lambda tmp: SqliteCommentStore(tmp / "comments.sqlite"))]


@pytest.fixture(params=CommentStoreContract.adapters(), ids=[n for n, _ in CommentStoreContract.adapters()])
def cstore(request, bench_tmp):
    return request.param[1](bench_tmp)


def test_a_comment_anchors_to_a_run_and_reads_back(cstore):
    cstore.add(run="abc123", anchor="candidate:4-F", author="mg", body="nitro on an aniline")
    got = cstore.for_run("abc123")
    assert [c.body for c in got] == ["nitro on an aniline"]
    assert got[0].anchor == "candidate:4-F"


def test_comments_for_an_unknown_run_are_empty_not_an_error(cstore):
    assert cstore.for_run("nope") == []
