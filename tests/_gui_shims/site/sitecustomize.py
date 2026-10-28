"""TEMPORARY: with this directory on PYTHONPATH, every Python process (marimo's kernels included) starts with the
shims installed. Only for testing the app before the real modules are merged; delete with the shims."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _gui_shims import install  # noqa: E402

install()
