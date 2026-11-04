"""The studies API behind the campaign panel. Studies are configured module paths, never discovered."""

STUDY = "bench.study_fixture"


def test_dag_rows_are_in_order(client_for):
    c, _ = client_for(studies=[STUDY])
    rows = c.get(f"/api/studies/{STUDY}/dag").json()
    assert [r["order"] for r in rows] == sorted(r["order"] for r in rows) and rows[0]["step"] == "parent"


def test_cells_cover_every_condition_and_step_and_start_to_run(client_for):
    """Data source: the fixture study has one step over x in {1, 2} and an empty cache, so two cells, both to run."""
    c, _ = client_for(studies=[STUDY])
    cells = c.get(f"/api/studies/{STUDY}/cells").json()
    assert sorted((x["label"], x["step"], x["state"]) for x in cells) == [
        ("x=1", "parent", "to_run"),
        ("x=2", "parent", "to_run"),
    ]


def test_a_cached_cell_is_reported_cached(client_for):
    """Run the study into the configured cache, then the cells must say cached: the cache is real, not a fresh one."""
    import importlib

    from smeltery.study.cache import Cache

    c, ports = client_for(studies=[STUDY])
    study = importlib.import_module(STUDY).study
    study.run(Cache(ports["settings"].cache_dir))
    assert {x["state"] for x in c.get(f"/api/studies/{STUDY}/cells").json()} == {"cached"}


def test_an_unconfigured_or_unimportable_study_is_404_naming_it(client_for):
    c, _ = client_for(studies=["nope.nothing"])
    r = c.get("/api/studies/nope.nothing/dag")
    assert r.status_code == 404 and "nope.nothing" in r.json()["detail"]
    assert c.get(f"/api/studies/{STUDY}/dag").status_code == 404
