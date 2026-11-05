"""Workspace method defaults on disk: ROOT/.smeltery/bench/methods.toml, one table per method.

The file is edited as text through `smeltery.gui.config_text`, so comments a user adds to it survive a save.
"""

from __future__ import annotations

import os
import tempfile
import threading
import tomllib
from pathlib import Path
from typing import Any

from smeltery.gui import config_text

HEADER = (
    "# Workspace defaults for each method's settings (smeltery bench).\n"
    "# A default is copied into a step when the method is added to a pipeline; changing it here\n"
    "# never changes a pipeline that already has the step.\n"
)


class FilesystemMethodDefaults:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _text(self) -> str:
        try:
            return self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    def all(self) -> dict[str, dict[str, Any]]:
        cfg = tomllib.loads(self._text())
        return {k: dict(v) for k, v in cfg.items() if isinstance(v, dict) and v}

    def get(self, method_key: str) -> dict[str, Any]:
        return self.all().get(method_key, {})

    def put(self, method_key: str, values: dict[str, Any]) -> None:
        with self._lock:
            text = self._text() or HEADER
            cfg = tomllib.loads(text)
            if values:
                cfg[method_key] = dict(values)
            else:
                cfg.pop(method_key, None)
            out = config_text.apply(text, cfg)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".methods-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                    f.write(out)
                os.replace(tmp, self.path)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
