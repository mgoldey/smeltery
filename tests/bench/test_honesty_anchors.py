"""API-level honesty anchors. The browser-level ones (rendering) live in ui/e2e/."""

from smeltery.gui.results import load_run, resolved_matrix


def test_the_served_resolved_matrix_is_resolved_matrix_exactly(client_for, run_dir):
    c, _ = client_for(run_dir)
    names, matrix = resolved_matrix(load_run(run_dir))
    assert c.get("/api/runs/0/resolved").json() == {"names": names, "matrix": matrix}


def test_unranked_has_no_winner_anywhere_in_the_api(client_for, unranked_run_dir):
    c, _ = client_for(unranked_run_dir)
    assert c.get("/api/runs/0/verdict").json()["winner"] is None
    assert c.get("/api/runs/0/ddE").json()["verdict"]["winner"] is None


def test_the_floor_and_its_source_travel_with_every_table(client_for, run_dir):
    c, _ = client_for(run_dir)
    t = c.get("/api/runs/0/ddE").json()
    assert t["floor"] is not None and t["floor_source"]


def test_no_composite_score_column(client_for, run_dir):
    c, _ = client_for(run_dir)
    cols = set(c.get("/api/runs/0/ddE").json()["rows"][0])
    assert not {"score", "overall", "composite"} & {x.lower() for x in cols}
