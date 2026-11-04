from __future__ import annotations

import pytest


@pytest.fixture
def bench_tmp(tmp_path):
    return tmp_path
