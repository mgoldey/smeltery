"""Bench's request guard: a Host allow-list, an Origin check on unsafe methods, and a token off loopback.

The Host check stops DNS rebinding; the Origin check stops a foreign page posting to the API; the token stops anyone
else on the network once Bench binds beyond loopback.
"""

import pytest
from fastapi.testclient import TestClient

import smeltery.bench.app as appmod
from smeltery.bench.app import build_app, main
from smeltery.bench.security import SecurityConfig, is_loopback

LOCAL = "http://127.0.0.1:8765"
TOKEN = "s3cret-token-value"


@pytest.fixture
def client(tmp_path):
    def make(**security):
        app = build_app(tmp_path / "root", web_dir=tmp_path / "no-web", **security)
        return TestClient(app, base_url=LOCAL)

    (tmp_path / "root").mkdir()
    return make


def test_loopback_names():
    for h in ("localhost", "127.0.0.1", "127.0.0.2", "::1", "[::1]", "LOCALHOST"):
        assert is_loopback(h), h
    for h in ("0.0.0.0", "::", "192.168.1.5", "evil.example"):
        assert not is_loopback(h), h


def test_a_foreign_host_is_400(client):
    c = client()
    assert c.get("/api/tasks").status_code == 200
    r = c.get("/api/tasks", headers={"Host": "evil.example:8765"})
    assert r.status_code == 400 and "evil.example" in r.json()["reason"]
    assert c.get("/", headers={"Host": "evil.example"}).status_code == 400


@pytest.mark.parametrize("host", ["localhost:8765", "127.0.0.1", "[::1]:8765", "LocalHost:1"])
def test_loopback_hosts_are_allowed(client, host):
    assert client().get("/api/tasks", headers={"Host": host}).status_code == 200


def test_the_bound_host_and_extra_hosts_are_allowed(client):
    c = client(host="10.0.0.7", token=TOKEN, allowed_hosts=("bench.lab",))
    auth = {"Authorization": f"Bearer {TOKEN}"}
    assert c.get("/api/tasks", headers={**auth, "Host": "10.0.0.7:8765"}).status_code == 200
    assert c.get("/api/tasks", headers={**auth, "Host": "bench.lab"}).status_code == 200
    assert c.get("/api/tasks", headers={**auth, "Host": "other.lab"}).status_code == 400


def test_a_foreign_origin_on_post_is_403_and_same_origin_works(client):
    c = client()
    bad = c.post("/api/tasks/ghost/cancel", headers={"Origin": "http://evil.example"})
    assert bad.status_code == 403 and "evil.example" in bad.json()["reason"]
    assert c.post("/api/tasks/ghost/cancel", headers={"Origin": "null"}).status_code == 403
    # Same origin and no Origin at all (curl) reach the route, which answers 404 for an unknown task.
    assert c.post("/api/tasks/ghost/cancel", headers={"Origin": LOCAL}).status_code == 404
    assert c.post("/api/tasks/ghost/cancel").status_code == 404
    # A GET with a foreign Origin is not refused by the Origin check: it changes nothing and CORS hides the answer.
    assert c.get("/api/tasks", headers={"Origin": "http://evil.example"}).status_code == 200


def test_the_dns_rebinding_probe_is_refused(client):
    """The review's probe: a foreign Host and Origin posting to the API."""
    r = client().post("/api/tasks/ghost/cancel", headers={"Host": "evil.example:8765", "Origin": "http://evil.example"})
    assert r.status_code == 400


def test_post_api_tasks_no_longer_exists(client):
    c = client()
    r = c.post("/api/tasks", json={"command": ["touch", "/tmp/pwned"]})
    assert r.status_code in (404, 405)
    assert (
        "/api/tasks" not in c.get("/openapi.json").json()["paths"]
        or "post" not in (c.get("/openapi.json").json()["paths"]["/api/tasks"])
    )


def test_with_a_token_the_api_needs_it(client):
    c = client(token=TOKEN)
    assert c.get("/api/tasks").status_code == 401
    assert c.get("/api/tasks", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert c.get("/api/tasks", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
    assert c.get("/healthz").status_code == 200  # liveness stays open for probes


def test_the_token_url_sets_a_cookie_the_browser_then_carries(client):
    c = client(token=TOKEN)
    assert c.get("/?token=wrong", follow_redirects=False).status_code == 401
    r = c.get(f"/?token={TOKEN}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert c.get("/api/tasks").status_code == 200  # the TestClient keeps the cookie, as a browser does


def test_without_a_token_loopback_needs_no_auth(client):
    assert client().get("/api/tasks").status_code == 200


def test_a_non_loopback_bind_without_a_token_exits_2(monkeypatch, tmp_path, capsys):
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<div id=root></div>")
    monkeypatch.setattr(appmod, "WEB_DIR", web)
    monkeypatch.delenv("SMELTERY_BENCH_TOKEN", raising=False)
    assert main([str(tmp_path), "--host", "0.0.0.0"]) == 2
    err = capsys.readouterr().err
    assert "--token" in err and "SMELTERY_BENCH_TOKEN" in err


def test_security_config_hosts():
    cfg = SecurityConfig(bind_host="0.0.0.0", allowed_hosts=("Bench.Lab",))
    assert {"localhost", "127.0.0.1", "::1", "bench.lab"} <= cfg.hosts()
