"""Workspace method defaults on disk: ROOT/.smeltery/bench/methods.toml, one table per method.

The file is edited as text through `smeltery.config_text`, so comments a user adds to it survive a save.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from smeltery import config_text

from ._files import atomic_write

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
        return {k: dict(v) for k, v in self._cfg(self._text()).items() if isinstance(v, dict) and v}

    def _cfg(self, text: str) -> dict:
        return config_text.loads(text, what=str(self.path))

    def get(self, method_key: str) -> dict[str, Any]:
        return self.all().get(method_key, {})

    def put(self, method_key: str, values: dict[str, Any]) -> None:
        with self._lock:
            text = self._text() or HEADER
            cfg = self._cfg(text)
            if values:
                cfg[method_key] = dict(values)
            else:
                cfg.pop(method_key, None)
            out = config_text.apply(text, cfg)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(self.path, out, prefix=".methods-")
