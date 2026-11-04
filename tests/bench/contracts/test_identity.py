import pytest

from bench.contracts.base import PortContract
from smeltery.bench.adapters.identity import LocalIdentity
from smeltery.bench.ports import Identity


class IdentityContract(PortContract):
    port = Identity

    @classmethod
    def adapters(cls):
        return [("LocalIdentity", lambda tmp: LocalIdentity())]


@pytest.fixture(params=IdentityContract.adapters(), ids=[n for n, _ in IdentityContract.adapters()])
def adapter(request, bench_tmp):
    return request.param[1](bench_tmp)


def test_current_returns_a_nonempty_string(adapter):
    assert isinstance(adapter.current(), str) and adapter.current()


def test_current_is_stable_within_a_process(adapter):
    assert adapter.current() == adapter.current()


def test_adapter_satisfies_the_protocol(adapter):
    assert isinstance(adapter, Identity)
