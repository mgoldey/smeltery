"""The version comes from the git tag, not from a committed string."""

import tomllib
from importlib.metadata import version
from pathlib import Path

import smeltery

PYPROJECT = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())


def test_version_is_dynamic_and_vcs_derived():
    assert "version" not in PYPROJECT["project"]
    assert "version" in PYPROJECT["project"]["dynamic"]
    assert PYPROJECT["tool"]["hatch"]["version"]["source"] == "vcs"
    assert any(r.startswith("hatch-vcs") for r in PYPROJECT["build-system"]["requires"])


def test_runtime_version_is_the_installed_metadata_version():
    assert smeltery.__version__ == version("smeltery")
    assert smeltery.__version__ not in ("", "0.0.1")
