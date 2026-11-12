"""B11: every 4xx/5xx the Bench API returns is one `Problem` shape, and the schema says so."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from smeltery.bench.api.models import Problem
from smeltery.bench.app import create_app
from smeltery.bench.openapi import schema


def _client(tmp_path):
    from smeltery.bench.adapters.identity import LocalIdentity
    from smeltery.bench.adapters.run_store import FilesystemRunStore
    from smeltery.bench.app import Settings
    from smeltery.bench.ports.clock import SystemClock

    class Boom:
        def list(self, status=None):
            raise RuntimeError("queue down")

    app = create_app(
        run_store=FilesystemRunStore(tmp_path),
        task_queue=Boom(),
        identity=LocalIdentity(),
        clock=SystemClock(),
        settings=Settings(root=tmp_path, web_dir=tmp_path / "no-web"),
    )
    return TestClient(app, raise_server_exceptions=False)


def _is_problem(res) -> None:
    assert res.status_code >= 400
    assert res.headers["content-type"].startswith("application/json"), res.text
    p = Problem.model_validate(res.json())
    assert p.reason


def test_http_exception_is_a_problem(tmp_path):
    res = _client(tmp_path).get("/api/runs/nope/nothing")
    _is_problem(res)


def test_validation_error_is_a_readable_problem(tmp_path):
    res = _client(tmp_path).get("/api/tasks/x/log", params={"lines": "many"})
    assert res.status_code == 422
    _is_problem(res)
    body = res.json()
    assert "lines" in body["reason"] and not body["reason"].startswith("[")
    assert isinstance(body["errors"], list)


def test_route_returned_problem_and_unhandled_exception(tmp_path):
    c = _client(tmp_path)
    _is_problem(c.get("/api/tasks"))  # the dead queue: a 503 Problem


def test_unhandled_exception_is_a_problem(tmp_path):
    c = _client(tmp_path)

    @c.app.get("/api/_boom")
    def boom():
        raise RuntimeError("secret detail")

    res = c.get("/api/_boom")
    assert res.status_code == 500
    _is_problem(res)
    assert "secret detail" not in res.text


SCHEMA = schema()
OPS = [(path, method) for path, item in SCHEMA["paths"].items() for method in item if method != "parameters"]


@pytest.mark.parametrize(("path", "method"), OPS, ids=[f"{m.upper()} {p}" for p, m in OPS])
def test_every_operation_documents_problem(path, method):
    responses = SCHEMA["paths"][path][method]["responses"]
    assert responses["default"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/Problem"}
    for status, r in responses.items():
        if status == "default" or status.startswith("2"):
            continue
        ref = r["content"]["application/json"]["schema"].get("$ref", "")
        assert ref.rsplit("/", 1)[-1] in {"Problem", "ImportRefused"}, (status, ref)
    assert "HTTPValidationError" not in SCHEMA["components"]["schemas"]


def test_every_route_error_body_validates(tmp_path):
    """Hit each documented route with no input: whatever 4xx/5xx comes back must be a Problem."""
    c = _client(tmp_path)
    seen = 0
    for path, method in OPS:
        url = path.replace("{", "").replace("}", "")  # path params become literal text
        res = c.request(method.upper(), url)
        if res.status_code >= 400:
            _is_problem(res)
            seen += 1
    assert seen > 20
