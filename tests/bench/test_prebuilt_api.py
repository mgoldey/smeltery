"""Prebuilt pipelines over HTTP: the list, and starting a workspace pipeline from one."""

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bench.fakes import FakeIdentity, FakeTaskQueue, FixedClock
from smeltery.bench.adapters.run_store import FilesystemRunStore
from smeltery.bench.api.prebuilt import NOT_INPUTS
from smeltery.bench.app import Settings, create_app

NAMES = ["benzoic-pbe", "benzoic-rhf", "danuglipron-halogen", "docking", "structure"]
PREBUILT = Path(__file__).resolve().parents[2] / "src" / "smeltery" / "prebuilt"


def _tree(folder: Path, skip: tuple[str, ...] = ()) -> dict[str, str]:
    return {
        str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(folder.rglob("*"))
        if p.is_file() and not set(p.relative_to(folder).parts) & set(skip)
    }


def _real_client(tmp_path):
    root = tmp_path / "bench-root"
    app = create_app(
        run_store=FilesystemRunStore(root),
        task_queue=FakeTaskQueue(),
        identity=FakeIdentity("mg"),
        clock=FixedClock(),
        settings=Settings(root=root, studies=(), web_dir=tmp_path / "no-web"),
    )
    return TestClient(app), root


def test_the_route_lists_every_prebuilt_with_their_three_lines_and_the_example_verdict(client_for):
    c, _ = client_for()
    got = c.get("/api/prebuilt")
    assert got.status_code == 200, got.text
    by = {p["name"]: p for p in got.json()}
    assert sorted(by) == NAMES
    for n in NAMES:
        three = (PREBUILT / n / "about.md").read_text().strip().splitlines()
        assert [by[n]["about"], by[n]["needs"], by[n]["teaches"]] == three
    assert {n: by[n]["verdict"]["state"] for n in NAMES} == {
        "benzoic-pbe": "RESOLVED",
        "benzoic-rhf": "RESOLVED",
        "danuglipron-halogen": "UNRANKED",  # the record: keep=3 falls inside an unresolved group
        "docking": "UNRANKED",
        "structure": "UNRANKED",
    }
    assert "pdb2pqr30" in by["docking"]["needs"]


def test_the_route_returns_the_curated_order_not_the_alphabet(client_for):
    """`names()` sorts, which puts benzoic-pbe above benzoic-rhf; the landing page's first card must be the
    teaching example, so the route returns the order each prebuilt's gallery.toml declares."""
    from smeltery.bench.api.prebuilt import ordered_names

    c, _ = client_for()
    order = ordered_names()
    assert [p["name"] for p in c.get("/api/prebuilt").json()] == order
    assert order[0] == "benzoic-rhf"
    assert sorted(order) == NAMES  # alphabetical would have been a different list


def test_every_prebuilt_is_ranked_and_tiered_deliberately():
    """A sixth prebuilt must be given a place, not appear silently at the end of the list."""
    from smeltery.bench.api.prebuilt import names, ordered_names, places

    assert set(ordered_names()) == set(names()) == set(places())


def test_where_a_prebuilt_sits_is_declared_beside_it_not_listed_in_library_code():
    """Library code must not name a campaign target (tests/test_migration_rules.py, issue #8). A tuple of every
    shipped example here would have to, so each example declares its own rank and tier in its own folder."""
    from smeltery.bench.api import prebuilt as P

    source = Path(P.__file__).read_text()
    for token in ("danuglipron", "7LCJ", "GLP1R"):
        assert token not in source, f"{token!r} is back in library code"
    for n in NAMES:  # ...and every shipped example really does carry its placement
        assert (PREBUILT / n / "gallery.toml").is_file(), n


def test_each_tier_has_one_card_and_each_variant_names_its_parent(client_for):
    c, _ = client_for()
    got = {p["name"]: p for p in c.get("/api/prebuilt").json()}
    cards = [p for p in got.values() if p["tier"] != "variant"]
    assert [p["name"] for p in cards] == ["benzoic-rhf", "docking", "danuglipron-halogen"]
    assert [p["tier"] for p in cards] == ["start_here", "teaches_unranked", "real_campaign"]
    variants = {n: p["variant_of"] for n, p in got.items() if p["tier"] == "variant"}
    assert variants == {"benzoic-pbe": "benzoic-rhf", "structure": "docking"}
    assert all(p["variant_of"] is None for p in cards)
    assert set(variants.values()) <= set(got)  # a variant never points at a prebuilt that is not shipped


def test_the_third_line_states_the_example_runs_outcome(client_for):
    """`teaches` is what makes an UNRANKED example read as a lesson, so it has to agree with the verdict."""
    c, _ = client_for()
    for p in c.get("/api/prebuilt").json():
        teaches = p["teaches"].lower()
        assert teaches, p["name"]
        want = "resolves" if p["verdict"]["state"] == "RESOLVED" else "does not resolve"
        assert teaches.startswith(want), (p["name"], p["teaches"])


def test_starting_from_a_prebuilt_copies_it_and_leaves_it_alone(tmp_path):
    c, root = _real_client(tmp_path)
    before = _tree(PREBUILT)
    for n in NAMES:
        r = c.post("/api/pipelines", json={"name": f"my-{n}", "from_prebuilt": n})
        assert r.status_code == 201, r.text
        doc = r.json()
        # R15: the text is the packaged pipeline.toml exactly, with no workspace defaults copied in
        assert doc["toml"].encode() == (PREBUILT / n / "pipeline.toml").read_bytes()
        folder = Path(doc["folder"])
        assert (folder / "config.toml").read_bytes() == (PREBUILT / n / "pipeline.toml").read_bytes()
        # Every input the config reads is copied; the example's own metadata (the blurb, where it sits in the
        # gallery) and its runs are not, because they describe the shipped example rather than the user's copy.
        for f in (PREBUILT / n).iterdir():
            if f.name not in NOT_INPUTS:
                assert (folder / f.name).read_bytes() == f.read_bytes()
            elif f.is_file():
                assert not (folder / f.name).exists(), f"{n}: {f.name} describes the example, not the copy"
        if (PREBUILT / n / "run").exists():  # the example run, without a --temporal run's scratch or live marker
            assert _tree(PREBUILT / n / "run", skip=("temporal", "running.json")) == _tree(folder / "run")
        assert not (folder / "imported").exists()
        # edit the copy: save over it, then change its run directory
        put = c.put(f"/api/pipelines/my-{n}", json={"toml": doc["toml"] + "# edited\n", "base_sha256": doc["sha256"]})
        assert put.status_code == 200, put.text
        for p in (folder / "run").glob("run-*.json"):
            p.write_text("{}")
    assert _tree(PREBUILT) == before


def test_the_example_run_is_listed_as_a_run_under_the_new_pipeline(tmp_path):
    c, root = _real_client(tmp_path)
    assert c.get("/api/runs").json() == []
    doc = c.post("/api/pipelines", json={"name": "pbe", "from_prebuilt": "benzoic-pbe"}).json()
    runs = c.get("/api/runs").json()
    assert len(runs) == 1
    assert runs[0]["path"].startswith(str(Path(doc["folder"]) / "run"))
    assert runs[0]["verdict"]["state"] == "RESOLVED"
    # the copied pipeline is complete: no step wants input, and none is stale or changed
    steps = c.post("/api/pipelines/steps", json={"toml": doc["toml"]}).json()["steps"]
    assert {s["state"] for s in steps} <= {"ready", "off"} and "ready" in {s["state"] for s in steps}


def test_an_unknown_prebuilt_or_a_taken_name_is_refused(tmp_path):
    c, _ = _real_client(tmp_path)
    r = c.post("/api/pipelines", json={"name": "x", "from_prebuilt": "nope"})
    assert r.status_code == 400 and "unknown prebuilt" in r.json()["reason"]
    assert c.post("/api/pipelines", json={"name": "x", "from_prebuilt": "docking"}).status_code == 201
    assert c.post("/api/pipelines", json={"name": "x", "from_prebuilt": "docking"}).status_code == 409


def test_copy_plan_leaves_the_imported_record_behind():
    from smeltery.bench.api.prebuilt import copy_plan

    plan = copy_plan("danuglipron-halogen")
    assert not [r for r in plan if r.split("/")[0] == "imported"]
    assert {"7LCJ_pocket.pdb", "conf_00_cryo_em.xyz"} <= set(plan)


def test_copy_plan_leaves_a_temporal_runs_scratch_and_live_marker_behind():
    """A --temporal run keeps OUT/temporal/ (its poses and fields) and OUT/temporal/running.json (the workflow it
    belongs to) while it runs: a copy holds the example run's record and log, never those."""
    from smeltery.bench.api.prebuilt import _copied, copy_plan

    assert _copied("run/calcs.jsonl") and _copied("run/run-abc.json") and _copied("run/inputs/x/y.pdb")
    assert not _copied("run/temporal/work.json") and not _copied("run/temporal/running.json")
    assert not _copied("run/running.json") and not _copied("imported/run-x.json") and not _copied("about.md")
    for n in NAMES:
        assert not [r for r in copy_plan(n) if "temporal" in r.split("/")[1:] or r.endswith("running.json")]


def test_the_guard_refuses_a_prebuilt_nobody_ranked(monkeypatch):
    """A sixth folder in the wheel is a deliberate decision about the landing page, not a silent sixth row."""
    from smeltery.bench.api import prebuilt as P

    monkeypatch.setattr(P, "names", lambda: [*NAMES, "sixth"])
    with pytest.raises(RuntimeError, match="ships no gallery.toml"):
        P.ordered_names()


def test_the_guard_refuses_a_variant_pointing_at_a_variant(monkeypatch):
    from smeltery.bench.api import prebuilt as P

    real = P._place
    monkeypatch.setattr(
        P,
        "_place",
        lambda n: P.Place(rank=4, tier="variant", variant_of="benzoic-pbe") if n == "structure" else real(n),
    )
    with pytest.raises(RuntimeError, match="not a card of its own"):
        P.ordered_names()


def test_the_guard_refuses_two_prebuilts_claiming_one_rank(monkeypatch):
    """Two examples at the same rank would order themselves by whatever sorted() happened to do."""
    from smeltery.bench.api import prebuilt as P

    real = P._place
    monkeypatch.setattr(
        P,
        "_place",
        lambda n: P.Place(rank=1, tier="variant", variant_of="benzoic-rhf") if n == "benzoic-pbe" else real(n),
    )
    with pytest.raises(RuntimeError, match="both claim rank 1"):
        P.ordered_names()


def test_the_guard_refuses_a_placement_that_is_not_a_tier(monkeypatch, tmp_path):
    """gallery.toml is data in the wheel, so a typo in it must be reported, not silently accepted."""
    from smeltery.bench.api import prebuilt as P

    bad = tmp_path / "benzoic-rhf"
    bad.mkdir()
    (bad / "gallery.toml").write_text('rank = 1\ntier = "startt_here"\n')
    monkeypatch.setattr(P, "root", lambda: tmp_path)
    with pytest.raises(RuntimeError, match="tier must be one of"):
        P._place("benzoic-rhf")
