"""`python -m smeltery.bench.openapi`: print Bench's OpenAPI schema as JSON.

`ui/scripts/gen-api.mjs` turns it into `ui/src/api/schema.d.ts`, so the frontend's request and response types are
generated from the API's pydantic models and cannot drift from them. The ports here are throwaway: only the routes
and their models reach the schema.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


def schema() -> dict:
    from .adapters.identity import LocalIdentity
    from .adapters.run_store import FilesystemRunStore
    from .app import Settings, create_app
    from .ports.clock import SystemClock

    root = Path(tempfile.mkdtemp())

    class _NoQueue:
        def list(self, status=None):
            return []

    app = create_app(
        run_store=FilesystemRunStore(root),
        task_queue=_NoQueue(),
        identity=LocalIdentity(),
        clock=SystemClock(),
        settings=Settings(root=root, web_dir=root / "no-web"),
    )
    return app.openapi()


def main() -> int:
    json.dump(schema(), sys.stdout, indent=1, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
