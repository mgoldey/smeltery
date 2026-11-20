"""The Temporal server the workflow tests talk to (tests/test_temporal_run.py, tests/test_job_backends.py,
tests/test_temporal_campaign.py): found, started, or the reason there is none.

In order:

1. a server already listening at $SMELTERY_TEMPORAL_ADDRESS (default 127.0.0.1:7233), e.g. `temporal server
   start-dev` or the compose profile;
2. with SMELTERY_TEST_TEMPORAL_LOCAL=1, `temporalio.testing.WorkflowEnvironment.start_local()`: the dev server as a
   child of this test process, on a free port, stopped when the process exits. It downloads the server on first use
   (from temporal.download), unless $SMELTERY_TEST_TEMPORAL_CLI names a `temporal` CLI binary already on disk, which is
   then used as is (an offline machine, or a sandbox whose proxy refuses temporal.download);
3. none: `address()` returns None and `reason()` says why; a test skips with it.

SMELTERY_TEST_TEMPORAL_REQUIRE=1 (CI's `temporal` job) turns that skip into a failure, so a job meant to run these
tests cannot pass by skipping them.

Why the dev server and not the time-skipping test server: the tests run real score children (the fake engine, in their
own process groups) that sleep, hang and are killed in wall-clock time; time skipping would not shorten those, and
it needs a second binary (temporal-test-server) the dev server does not. Timeouts and heartbeats in the tests are
seconds, so the dev server is fast enough.
"""

from __future__ import annotations

import asyncio
import atexit
import os
import socket

import pytest

DEFAULT = os.environ.get("SMELTERY_TEMPORAL_ADDRESS", "127.0.0.1:7233")
_state: dict = {}


def _listening(address: str) -> bool:
    host, _, port = address.rpartition(":")
    try:
        with socket.create_connection((host, int(port)), timeout=1):
            return True
    except (OSError, ValueError):
        return False


def address() -> str | None:
    """The address of a Temporal server for this test process, or None (see `reason()`). Started at most once."""
    if "address" in _state:
        return _state["address"]
    found: str | None = None
    if _listening(DEFAULT):
        found = DEFAULT
    elif os.environ.get("SMELTERY_TEST_TEMPORAL_LOCAL") != "1":
        _state["reason"] = f"no Temporal server at {DEFAULT} (SMELTERY_TEST_TEMPORAL_LOCAL=1 starts one)"
    else:
        try:
            from temporalio.testing import WorkflowEnvironment
        except ImportError:
            _state["reason"] = "temporalio (the `temporal` extra) is not installed"
        else:
            existing = os.environ.get("SMELTERY_TEST_TEMPORAL_CLI") or None
            try:
                env = asyncio.run(WorkflowEnvironment.start_local(dev_server_existing_path=existing))
            except RuntimeError as e:
                _state["reason"] = f"WorkflowEnvironment.start_local() failed: {e}"
            else:
                _state["env"] = env
                atexit.register(_stop)
                found = env.client.service_client.config.target_host
                # what `smeltery run --temporal`, the job backend and the workers default to (smeltery.settings)
                os.environ["SMELTERY_TEMPORAL_ADDRESS"] = found
    _state["address"] = found
    return found


def reason() -> str:
    return _state.get("reason", "")


def _stop() -> None:
    env = _state.pop("env", None)
    if env is not None:
        try:
            asyncio.run(env.shutdown())
        except Exception:  # the process is exiting; the server is its child either way
            pass


def require() -> str:
    """The server's address, or skip (fail under SMELTERY_TEST_TEMPORAL_REQUIRE=1) naming why there is none."""
    found = address()
    if found is None:
        if os.environ.get("SMELTERY_TEST_TEMPORAL_REQUIRE") == "1":
            pytest.fail(f"SMELTERY_TEST_TEMPORAL_REQUIRE=1 but {reason()}")
        pytest.skip(reason())
    return found
