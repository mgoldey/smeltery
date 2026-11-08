"""StructureProvider (#16): provenance travels with the structure, and nothing is fabricated."""

import json
import os
import urllib.error
from pathlib import Path

import pytest

from smeltery.providers import (
    AFDB_ATTRIBUTION,
    AfdbProvider,
    PdbProvider,
    StructureProvider,
    StructureResult,
    plddt_from_pdb,
)

DATA = Path(__file__).parent / "data"
AFDB_PDB = (DATA / "afdb_tiny.pdb").read_text()
ENTRY = [{"pdbUrl": "https://example.test/AF-P00000-F1-model_v4.pdb", "latestVersion": 4, "entryId": "AF-P00000-F1"}]


def _afdb_fetch(url: str) -> bytes:
    if "api/prediction/P69905" in url:
        return json.dumps(ENTRY).encode()
    if url == ENTRY[0]["pdbUrl"]:
        return AFDB_PDB.encode()
    raise urllib.error.URLError(f"offline fixture has no {url}")


def test_afdb_result_carries_plddt_and_cc_by_attribution():
    res = AfdbProvider(fetch=_afdb_fetch).predict("P69905")
    assert res is not None and res.kind == "PREDICTED"
    assert res.plddt == ((1, 91.2), (2, 55.5), (3, 38.0))
    assert res.mean_plddt == pytest.approx((91.2 + 55.5 + 38.0) / 3)
    assert res.license_id == "CC-BY-4.0" and res.attribution == AFDB_ATTRIBUTION and "CC BY 4.0" in res.attribution
    d = res.to_dict()
    assert d["kind"] == "PREDICTED" and d["license_id"] and d["attribution"] and d["mean_plddt"]
    json.dumps(d)  # record-ready
    assert d["metadata"]["model_version"] == 4


def test_provider_that_cannot_answer_returns_none_and_says_why():
    p = AfdbProvider(fetch=_afdb_fetch)
    assert p.predict("not-an-accession") is None and "UniProt" in p.last_error
    assert p.predict("Q99999") is None and "failed" in p.last_error  # offline fixture lacks it
    q = PdbProvider(fetch=_afdb_fetch)
    assert q.predict("zzz") is None and q.last_error
    assert q.predict("1ABC") is None and "could not fetch" in q.last_error


def test_empty_or_atomless_text_is_not_a_structure(tmp_path):
    f = tmp_path / "empty.pdb"
    f.write_text("REMARK nothing here\nEND\n")
    p = PdbProvider()
    assert p.predict(str(f)) is None and "no ATOM" in p.last_error
    with pytest.raises(ValueError):
        StructureResult(text="  ", kind="EXPERIMENTAL", source="x")


def test_predicted_structure_cannot_be_labelled_experimental():
    with pytest.raises(ValueError, match="cannot be labelled EXPERIMENTAL"):
        StructureResult(text="ATOM", kind="EXPERIMENTAL", source="x", plddt=((1, 90.0),))
    with pytest.raises(ValueError, match="cannot be labelled EXPERIMENTAL"):
        StructureResult(text="ATOM", kind="EXPERIMENTAL", source="x", weights_source="boltz-2")
    with pytest.raises(ValueError, match="licence"):
        StructureResult(text="ATOM", kind="PREDICTED", source="x")


def test_pdb_provider_local_file_is_experimental():
    res = PdbProvider().predict(str(DATA / "gly3.pdb"))
    assert res is not None and res.kind == "EXPERIMENTAL" and res.plddt is None
    assert res.to_dict()["sha256"]


def test_pdb_provider_fetches_by_id_through_the_injected_fetcher():
    seen = []

    def fetch(url):
        seen.append(url)
        return (DATA / "gly3.pdb").read_bytes()

    res = PdbProvider(fetch=fetch).predict("1abc")
    assert seen == ["https://files.rcsb.org/download/1ABC.pdb"] and res.kind == "EXPERIMENTAL"


def test_providers_satisfy_the_protocol():
    assert isinstance(AfdbProvider(), StructureProvider) and isinstance(PdbProvider(), StructureProvider)


def test_plddt_reads_ca_only():
    assert len(plddt_from_pdb(AFDB_PDB)) == 3


@pytest.mark.skipif(os.environ.get("SMELTERY_NETWORK") != "1", reason="live AFDB; set SMELTERY_NETWORK=1")
def test_live_afdb_hemoglobin_beta():
    res = AfdbProvider().predict("P68871")
    assert res is not None and res.kind == "PREDICTED" and res.mean_plddt > 70


def _clock(monkeypatch, stamps):
    it = iter(stamps)
    monkeypatch.setattr("smeltery.providers.structure._utc_now", lambda: next(it))


def test_a_network_fetch_records_provider_and_fetch_time_but_the_dict_stays_timeless(monkeypatch):
    _clock(monkeypatch, ["2026-11-07T10:00:00Z", "2026-11-08T11:00:00Z"])
    a = AfdbProvider(fetch=_afdb_fetch).predict("P69905")
    b = AfdbProvider(fetch=_afdb_fetch).predict("P69905")
    assert (a.provider, a.fetched_utc) == ("afdb", "2026-11-07T10:00:00Z")
    assert a.fetch_record() == {"provider": "afdb", "source": ENTRY[0]["pdbUrl"], "fetched_utc": a.fetched_utc}
    assert a.to_dict()["provider"] == "afdb" and "fetched_utc" not in a.to_dict()
    assert a.to_dict() == b.to_dict()  # fetched a day apart, identical digestable dict


def test_rcsb_fetch_carries_the_header_revision_and_a_missing_header_is_just_omitted(monkeypatch):
    _clock(monkeypatch, ["2026-11-07T10:00:00Z", "2026-11-07T10:00:01Z"])
    header = "REVDAT   3   24-FEB-09 1ABC    1       VERSN\nREVDAT   2   01-JAN-05 1ABC    1       JRNL\n"
    atom = "ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00  0.00           N\n"
    r = PdbProvider(fetch=lambda url: (header + atom).encode()).predict("1abc")
    assert r.provider == "pdb" and r.fetched_utc == "2026-11-07T10:00:00Z"
    assert r.metadata == {"pdb_id": "1ABC", "revision": 3, "revision_date": "24-FEB-09"}
    bare = PdbProvider(fetch=lambda url: atom.encode()).predict("1abc")
    assert "revision" not in bare.metadata and bare.fetched_utc


def test_a_local_file_has_a_provider_but_no_fetch_time(tmp_path):
    f = tmp_path / "x.pdb"
    f.write_text("ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00  0.00           N\n")
    r = PdbProvider(fetch=lambda url: pytest.fail("network touched")).predict(str(f))
    assert r.provider == "pdb" and r.fetched_utc is None and r.fetch_record() is None
    assert r.to_dict()["metadata"] == {}


def test_a_failed_fetch_records_no_time(monkeypatch):
    _clock(monkeypatch, [])  # any call would raise StopIteration
    assert AfdbProvider(fetch=_afdb_fetch).predict("Q99999") is None
