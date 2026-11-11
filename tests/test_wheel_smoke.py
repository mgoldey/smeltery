"""scripts/wheel_smoke.py: its extras, the provenance rule, and the guard against an unsmoked extra (issue #31)."""

import importlib.util
import json
import tomllib
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("wheel_smoke", ROOT / "scripts" / "wheel_smoke.py")
ws = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ws)


def _fake_wheel(tmp_path, extras):
    p = tmp_path / "smeltery-9.9.9-py3-none-any.whl"
    meta = "Metadata-Version: 2.4\nName: smeltery\nVersion: 9.9.9\n" + "".join(f"Provides-Extra: {e}\n" for e in extras)
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("smeltery-9.9.9.dist-info/METADATA", meta)
    return p


def test_every_extra_in_pyproject_has_a_smoke_check_and_nothing_stale_is_left():
    declared = set(tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["optional-dependencies"])
    smoked = set(ws.EXTRA_CHECKS) - {"none"}
    assert declared - ws.SKIP_EXTRAS == smoked, (
        f"extras without a smoke check: {sorted(declared - ws.SKIP_EXTRAS - smoked)}; "
        f"smoke checks for extras that no longer exist: {sorted(smoked - declared)}. "
        "Edit EXTRA_CHECKS in scripts/wheel_smoke.py."
    )


def test_wheel_extras_come_from_the_wheels_own_metadata_and_exclude_dev(tmp_path):
    wheel = _fake_wheel(tmp_path, ["dev", "zeta", "alpha"])
    assert ws.wheel_extras(wheel) == ["alpha", "zeta"]
    assert json.loads(_run_main(["list-extras", str(wheel)])) == ["none", "alpha", "zeta"]


def _run_main(argv):
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert ws.main(argv) == 0
    return buf.getvalue()


@pytest.mark.parametrize(
    ("record", "ok"),
    [
        ({"ferric": {"source": "ferric.__build__", "provenance": "VERIFIED from ferric.__build__ (stamped)"}}, True),
        ({"ferric": {"source": "package index", "provenance": "VERIFIED: index release 0.1.0rc7 (immutable)"}}, True),
        ({"ferric": {"source": "package index", "provenance": "INFERRED from the local source directory"}}, False),
        ({"ferric": {"source": "ferric.__build__", "provenance": "INFERRED from something"}}, False),
        (
            {"ferric": {"source": "file:///x/ferric", "provenance": "VERIFIED from direct_url.json (git install)"}},
            False,
        ),
        ({"ferric": {"source": None, "provenance": "UNKNOWN"}}, False),
        ({}, False),
    ],
)
def test_only_a_verified_build_stamp_or_index_release_passes(record, ok):
    assert (ws.verified_provenance(record) is None) is ok


def test_an_extra_the_wheel_does_not_have_and_an_unsmoked_extra_are_refused_before_anything_is_installed(
    tmp_path, capsys
):
    wheel = _fake_wheel(tmp_path, ["real", "unsmoked"])
    assert ws.check(wheel, "bogus", False, None, None) == 2
    assert "not an extra" in capsys.readouterr().err
    assert ws.check(wheel, "unsmoked", False, None, None) == 2  # in the wheel but no EXTRA_CHECKS entry
    assert "no smoke checks defined" in capsys.readouterr().err


def test_the_sdist_ships_the_same_gitignored_artifacts_as_the_wheel():
    """`python -m build` builds the wheel FROM the sdist, so an artifact the sdist drops is absent from the wheel.

    `release.yml` runs plain `python -m build` (sdist + wheel); CI's wheel-build runs `--wheel`. That difference
    published nothing for v0.1.0: `src/smeltery/bench/web` is gitignored build output, hatch leaves ignored files out
    by default, and `artifacts` was declared only on the wheel target. The sdist therefore carried 0 of the 59 UI
    files, and the wheel built from it asserted `bench/web/index.html` missing. Both targets must list the same
    artifacts, or the two build commands disagree about what ships.
    """
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["hatch"]["build"]["targets"]
    wheel_artifacts = set(cfg.get("wheel", {}).get("artifacts", []))
    sdist_artifacts = set(cfg.get("sdist", {}).get("artifacts", []))
    assert wheel_artifacts, "the wheel target must declare the gitignored build output it ships"
    assert sdist_artifacts == wheel_artifacts, (
        "[tool.hatch.build.targets.sdist] artifacts must match the wheel's, or `python -m build` (which builds the "
        f"wheel from the sdist) ships a wheel without them. Missing from sdist: "
        f"{sorted(wheel_artifacts - sdist_artifacts)}; extra in sdist: {sorted(sdist_artifacts - wheel_artifacts)}."
    )


def test_both_targets_exclude_a_pipelines_local_temporal_scratch():
    """`artifacts` re-includes gitignored files, so `prebuilt/**` would ship an untracked run/temporal/ dir."""
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["hatch"]["build"]["targets"]
    for target in ("wheel", "sdist"):
        assert "**/run/temporal/**" in cfg[target].get("exclude", []), target
        # `artifacts` wins over `exclude`, so the negated artifact pattern is what actually keeps it out.
        assert "!src/smeltery/prebuilt/**/run/temporal/**" in cfg[target]["artifacts"], target


def test_a_heavy_extra_is_only_resolved_unless_full_is_asked_for():
    assert ws.HEAVY_EXTRAS, "the table of heavy extras must not be empty (ml-potential pulls torch)"
    heavy = next(iter(ws.HEAVY_EXTRAS))
    assert ws.resolves_only(heavy, False, False) is True
    assert ws.resolves_only(heavy, False, True) is False  # the release workflow: install it for real
    assert ws.resolves_only(heavy, True, True) is True  # an explicit --resolve-only always wins
    assert ws.resolves_only("docking", False, False) is False  # a light extra is always installed
    assert set(ws.HEAVY_EXTRAS) <= set(ws.EXTRA_CHECKS), "a heavy extra still needs its import checks for --full"
