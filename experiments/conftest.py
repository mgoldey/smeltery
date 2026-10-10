"""Environment skips for the frozen tests, kept here so the frozen files stay unedited.

Each entry is a frozen test that calls an external binary but carries no skip of its own (in ferric's
CI the binary was on PATH). Without it the test would FAIL for an environment reason, not a code reason.
"""

import shutil

import pytest

# tier3_gfn2 shells out to xtb
NEEDS_XTB = ("frozen_tools/pipeline/tests/test_tiers.py::test_a_legitimate_geometry_still_passes",)


def pytest_collection_modifyitems(config, items):
    if shutil.which("xtb") is not None:
        return
    skip = pytest.mark.skip(reason="the `xtb` binary is not on PATH; this frozen test has no skip of its own")
    for item in items:
        if item.nodeid.endswith(NEEDS_XTB):
            item.add_marker(skip)
