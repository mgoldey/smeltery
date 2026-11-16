"""`--read-only`: every write is refused with 403 in one place (the security middleware), reads and probes still work.

The walk is over the OpenAPI schema, so a route added later is covered without anyone remembering to list it. There
is deliberately no safelist: a POST that only computes (edit previews, structure lookups, impact) is refused too.
"""

import re

import pytest
from fastapi.testclient import TestClient

import smeltery.bench.app as appmod
from smeltery.bench.app import build_app, main
from smeltery.bench.security import UNSAFE_METHODS

LOCAL = "http://127.0.0.1:8765"


def _make(tmp_path, **kw):
    root = tmp_path / "root"
    root.mkdir(exist_ok=True)
    app = build_app(root, web_dir=tmp_path / "no-web", **kw)
    return app, TestClient(app, base_url=LOCAL)


def _operations(app):
    for path, item in app.openapi()["paths"].items():
        for method in item:
            if method.upper() in UNSAFE_METHODS | {"GET"}:
                yield method.upper(), path


def _fill(path):
    return re.sub(r"\{[^}]+\}", "1", path)


def _ops_for_param():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        app, _ = _make(Path(d))
        return list(_operations(app))


OPS = _ops_for_param()


@pytest.fixture(scope="module")
def ro(tmp_path_factory):
    """One read-only app for the whole walk: building Bench is the slow part, and the walk never writes."""
    return _make(tmp_path_factory.mktemp("ro"), read_only=True)[1]


@pytest.fixture(scope="module")
def rw(tmp_path_factory):
    return _make(tmp_path_factory.mktemp("rw"))[1]


UNSAFE = [o for o in OPS if o[0] in UNSAFE_METHODS]
SAFE_NO_PARAMS = [o for o in OPS if o[0] == "GET" and "{" not in o[1]]


def test_the_walk_found_the_mutating_routes():
    assert len(UNSAFE) > 25 and {m for m, _ in UNSAFE} >= {"POST", "PUT"}


@pytest.mark.parametrize("method,path", UNSAFE, ids=[f"{m} {p}" for m, p in UNSAFE])
def test_every_unsafe_operation_is_403_with_a_problem_body(ro, method, path):
    r = ro.request(method, _fill(path))
    assert r.status_code == 403
    body = r.json()
    assert set(body) == {"reason"} and "read-only" in body["reason"]


@pytest.mark.parametrize("method,path", UNSAFE[:3], ids=[f"{m} {p}" for m, p in UNSAFE[:3]])
def test_the_same_operation_is_not_403_when_writable(rw, method, path):
    assert rw.request(method, _fill(path)).status_code != 403


@pytest.mark.parametrize("path", [p for _, p in SAFE_NO_PARAMS])
def test_parameterless_gets_still_answer(ro, path):
    code = ro.get(path).status_code
    assert code < 500 and code != 403


def test_health_endpoints_still_answer(ro):
    assert ro.get("/healthz").json() == {"status": "ok"}
    assert ro.get("/livez").json() == {"status": "ok"}
    assert ro.get("/readyz").status_code in (200, 503)


def test_whoami_exposes_the_mode(ro, rw):
    assert ro.get("/api/whoami").json()["read_only"] is True
    assert rw.get("/api/whoami").json()["read_only"] is False


def test_a_next_round_is_refused_before_it_reads_anything(ro, rw):
    """The walk above covers POST /api/pipelines with an empty body; this sends the next_round mode's own body, so
    the one route that would write a decisions file into a new folder is named in the suite."""
    body = {"name": "round2", "next_round": {"run": "deadbeefcafe-000000", "drop": ["4-Cl"]}}
    r = ro.post("/api/pipelines", json=body)
    assert r.status_code == 403 and "read-only" in r.json()["reason"]
    assert rw.post("/api/pipelines", json=body).status_code != 403  # writable: refused on its merits, not the mode


def test_the_token_is_checked_before_read_only(tmp_path):
    _, c = _make(tmp_path, read_only=True, token="t0ken")
    assert c.post("/api/campaigns").status_code == 401
    assert c.post("/api/campaigns", headers={"authorization": "Bearer t0ken"}).status_code == 403


def test_a_foreign_origin_is_still_refused_as_such(ro):
    r = ro.post("/api/campaigns", headers={"origin": "http://evil.example"})
    assert r.status_code == 403 and "cross-origin" in r.json()["reason"]


@pytest.mark.parametrize(
    "argv,env,expected",
    [([], None, False), (["--read-only"], None, True), ([], "1", True), ([], "no", False)],
)
def test_the_flag_and_the_env_var_reach_build_app(monkeypatch, tmp_path, argv, env, expected):
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("x")
    monkeypatch.setattr(appmod, "WEB_DIR", web)
    monkeypatch.delenv(appmod.READ_ONLY_ENV, raising=False)
    if env is not None:
        monkeypatch.setenv(appmod.READ_ONLY_ENV, env)
    got = {}
    monkeypatch.setattr(appmod, "build_app", lambda root, **kw: got.update(kw))
    import logging

    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
    root = logging.getLogger()
    before, level = list(root.handlers), root.level
    try:
        assert main([str(tmp_path), *argv]) == 0
    finally:
        root.handlers[:] = before
        root.setLevel(level)
    assert got["read_only"] is expected
