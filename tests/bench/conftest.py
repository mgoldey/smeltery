"""Shared fixtures for the Bench API tests: contract-shaped run directories and a client over fake ports."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from gui_fixture import make_run_dir

from bench.fakes import FakeIdentity, FakeRunStore, FakeTaskQueue, FixedClock
from smeltery.bench.app import Settings, create_app


@pytest.fixture
def bench_tmp(tmp_path):
    return tmp_path


def record_path(out_dir: Path) -> Path:
    (p,) = Path(out_dir).glob("run-*.json")
    return p


@pytest.fixture
def run_dir(tmp_path):
    return make_run_dir(tmp_path)


@pytest.fixture
def unranked_run_dir(tmp_path):
    return make_run_dir(tmp_path, unranked=True)


@pytest.fixture
def incomplete_run_dir(tmp_path):
    out = make_run_dir(tmp_path)
    p = record_path(out)
    rec = json.loads(p.read_text())
    del rec["results"]["cut"]
    p.write_text(json.dumps(rec))
    return out


def run_id_of(client, i: int = 0) -> str:
    """The stable id of the i-th run the client lists (B04: what every route and body that addresses a run takes)."""
    return client.get("/api/runs").json()[i]["id"]


@pytest.fixture
def client_for(tmp_path):
    """client_for(run_dir=None, queue=None, studies=(), identity="mg") -> (TestClient, ports dict)."""

    def make(run_dir=None, queue=None, studies=(), identity="mg"):
        root = Path(run_dir) if run_dir is not None else tmp_path / "empty"
        root.mkdir(parents=True, exist_ok=True)
        ports = dict(
            run_store=FakeRunStore(root),
            task_queue=queue if queue is not None else FakeTaskQueue(),
            identity=FakeIdentity(identity),
            clock=FixedClock(),
            settings=Settings(root=tmp_path / "bench-root", studies=tuple(studies), web_dir=tmp_path / "no-web"),
        )
        return TestClient(create_app(**ports)), ports

    return make
