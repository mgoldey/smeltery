from bench.contracts.base import PortContract


def test_a_contract_subclass_with_no_adapter_is_skipped_not_passed():
    class Unregistered(PortContract):
        port = object

    assert Unregistered.adapters() == []


def test_a_contract_subclass_reports_its_registered_adapters():
    class Registered(PortContract):
        port = object

        @classmethod
        def adapters(cls):
            return [("fake", lambda tmp: object())]

    assert [n for n, _ in Registered.adapters()] == ["fake"]
