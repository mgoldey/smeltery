import pytest

from bench.contracts.base import PortContract
from smeltery.bench.adapters.artifact_store import FilesystemArtifactStore
from smeltery.bench.ports import ArtifactStore


class ArtifactStoreContract(PortContract):
    port = ArtifactStore

    @classmethod
    def adapters(cls):
        return [("FilesystemArtifactStore", lambda tmp: FilesystemArtifactStore(tmp / "artifacts"))]


@pytest.fixture(params=ArtifactStoreContract.adapters(), ids=[n for n, _ in ArtifactStoreContract.adapters()])
def astore(request, bench_tmp):
    return request.param[1](bench_tmp)


def test_bytes_round_trip_by_hash(astore, bench_tmp):
    p = bench_tmp / "f.txt"
    p.write_text("hello")
    h = astore.put(p)
    assert astore.get(h) == b"hello"


def test_a_tampered_artifact_is_refused(astore, bench_tmp):
    p = bench_tmp / "f.txt"
    p.write_text("hello")
    h = astore.put(p)
    astore.path_for(h).write_text("tampered")
    with pytest.raises(ValueError):
        astore.get(h)


def test_put_returns_the_sha256(astore, bench_tmp):
    import hashlib

    p = bench_tmp / "g.txt"
    p.write_text("hello")
    h = astore.put(p)
    assert h == hashlib.sha256(b"hello").hexdigest()


def test_put_accepts_raw_bytes(astore):
    h = astore.put(b"hello")
    assert astore.get(h) == b"hello"


def test_exists_reports_presence(astore, bench_tmp):
    p = bench_tmp / "h.txt"
    p.write_text("hello")
    h = astore.put(p)
    assert astore.exists(h) is True
    assert astore.exists("0" * 64) is False


def test_verify_matches_bytes(astore, bench_tmp):
    p = bench_tmp / "i.txt"
    p.write_text("hello")
    h = astore.put(p)
    assert astore.verify(h, b"hello") is True
    assert astore.verify(h, b"world") is False


def test_adapter_satisfies_the_protocol(astore):
    from smeltery.bench.ports import ArtifactStore

    assert isinstance(astore, ArtifactStore)


def test_a_digest_that_is_not_64_hex_is_refused(astore):
    for bad in ("../x", "../" * 3 + "etc/passwd", "ab", "A" * 64):
        with pytest.raises(ValueError):
            astore.path_for(bad)
        with pytest.raises(ValueError):
            astore.get(bad)
