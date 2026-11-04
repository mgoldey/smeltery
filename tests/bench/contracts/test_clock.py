from datetime import timezone

import pytest

from bench.contracts.base import PortContract
from smeltery.bench.adapters.clock import LocalClock
from smeltery.bench.ports import Clock


class ClockContract(PortContract):
    port = Clock

    @classmethod
    def adapters(cls):
        return [("LocalClock", lambda tmp: LocalClock())]


@pytest.fixture(params=ClockContract.adapters(), ids=[n for n, _ in ClockContract.adapters()])
def adapter(request, bench_tmp):
    return request.param[1](bench_tmp)


def test_now_is_timezone_aware_utc(adapter):
    assert adapter.now().tzinfo is not None
    assert adapter.now().utcoffset() == timezone.utc.utcoffset(None)


def test_now_is_monotonic_across_two_calls(adapter):
    assert adapter.now() <= adapter.now()
