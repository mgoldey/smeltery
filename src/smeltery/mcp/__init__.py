"""smeltery's MCP surface: four coarse screening tools (`smeltery mcp`, docs/mcp.md).

Importing this package pulls in nothing from the `mcp` extra: `smeltery.mcp.screening` and
`smeltery.mcp.targets` are plain smeltery, and only `smeltery.mcp.server` imports the SDK. `main` says so
with one line instead of a traceback when the extra is missing.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping

NOT_INSTALLED = "`smeltery mcp` needs the MCP SDK (the `mcp` extra): pip install 'smeltery[mcp]'"


def available() -> bool:
    """Whether the MCP SDK is importable. Checked, not cached: an extra can be installed mid-session."""
    from importlib.util import find_spec

    return find_spec("mcp.server.mcpserver") is not None


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    """`smeltery mcp`. 2 when the extra is missing or a setting is unusable."""
    if not available():
        print(f"smeltery mcp: {NOT_INSTALLED}", file=sys.stderr)
        return 2
    from .server import main as serve

    return serve(argv, env)
