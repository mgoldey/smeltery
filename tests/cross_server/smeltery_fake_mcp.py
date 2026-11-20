"""`smeltery mcp` on the fake engine: the server the cross-server transcript spawns over stdio.

    python tests/cross_server/smeltery_fake_mcp.py ROOT

This is `smeltery.mcp.server.main` with one difference: the local job backend's runner is
`tests/fake_ferric.py _job` (deterministic energies, `$FAKE_FERRIC` configures it) instead of the real engine, the
way tests/test_mcp_server.py builds its server in-process. Everything else -- the four tools, their schemas, the
refusals, the ceilings from `SMELTERY_*`, the stdio framing -- is the production server's, so the client sees what
an agent sees. CI has no quantum chemistry to spare, and the transcript is about the protocol, not the energies.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TESTS))

import fake_ferric  # noqa: E402

from smeltery import jobs  # noqa: E402
from smeltery.mcp.server import ToolContext, build_server  # noqa: E402
from smeltery.settings import resolve  # noqa: E402


def main(argv: list[str]) -> int:
    (root,) = argv
    runner = [sys.executable, str(Path(fake_ferric.__file__).resolve()), "_job"]
    backend = jobs.LocalJobBackend(root, runner=runner)
    server = build_server(ToolContext(resolve(os.environ), root=root, backend=backend))
    logging.basicConfig(stream=sys.stderr, level=os.environ.get("SMELTERY_MCP_LOG", "WARNING"))
    try:
        server.run("stdio")
    finally:
        backend.queue.stop_live("cross-server transcript: server exiting")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
