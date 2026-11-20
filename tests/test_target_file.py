"""Target definition files (`smeltery.target_file`, docs/targets.md): only the sections that describe a pocket,
`schema_version = 1`, and every problem refused loudly, naming the file."""

from __future__ import annotations

from pathlib import Path

import pytest

from smeltery import target_file
from smeltery.cli import main
from smeltery.target_file import TargetFileError, load

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "targets" / "two_site_pocket.toml"
SITE = "[[pocket.site]]\nq = 1.0\natom = 2\nfrom_atom = 1\ndistance = 3.0\n"


def _write(tmp_path: Path, text: str, name: str = "t.toml") -> Path:
    p = tmp_path / name
    p.write_text(text)
    return p


def test_the_committed_example_is_a_valid_target():
    sections = load(EXAMPLE)
    assert set(sections) == {"pocket"} and len(sections["pocket"]["site"]) == 2


def test_a_relative_pocket_file_resolves_against_the_target_file(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "pocket.pqr").write_text("ATOM\n")
    p = _write(tmp_path / "sub", 'schema_version = 1\n[pocket]\nfile = "pocket.pqr"\n[gates]\nposebusters = false\n')
    sections = load(p)
    assert sections["pocket"]["file"] == str((tmp_path / "sub" / "pocket.pqr").resolve())
    assert "schema_version" not in sections and sections["gates"] == {"posebusters": False}


@pytest.mark.parametrize(
    "text, needle",
    [
        (SITE, "has no schema_version"),
        ("schema_version = 2\n" + SITE, "schema_version = 1 only"),
        ("schema_version = true\n" + SITE, "schema_version = 1 only"),
        ('schema_version = 1\n[candidates]\nx = "CCO"\n' + SITE, "do not describe the target"),
        ("schema_version = 1\n[cut]\nkeep = 1\n" + SITE, "do not describe the target"),
        ("schema_version = 1\n[field_interaction]\nbasis = 'sto-3g'\n" + SITE, "do not describe the target"),
        ("schema_version = 1\n", "must describe the pocket"),
        ("schema_version = 1\n[gates]\nposebusters = true\n", "must describe the pocket"),
        ("schema_version = 1\n[pocket]\nfile = 'nowhere.pdb'\n", "does not exist"),
        ("schema_version = 1\n[pocket]\ncharge_model = 'x'\n" + SITE, "unknown key"),
        ("schema_version = 1\n[[pocket.site]]\nq = 1.0\natom = 2\n", "missing"),
        ("schema_version = 1\n[structure]\nspec = '1abc'\n", "exactly one of center"),
        ("schema_version = 1\n[gates]\nbogus = true\n" + SITE, "bogus"),
        ("schema_version = 1\npocket = 3\n", "must be a table"),
        ("schema_version = 1\n[pocket\n", "cannot read"),
    ],
    ids=[
        "no-version",
        "version-2",
        "version-bool",
        "ligands",
        "cut",
        "measurement",
        "empty",
        "gates-only",
        "missing-file",
        "unknown-pocket-key",
        "incomplete-site",
        "bad-structure",
        "unknown-gate",
        "section-not-table",
        "bad-toml",
    ],
)
def test_an_invalid_target_is_refused_naming_the_file_and_the_reason(tmp_path, text, needle):
    p = _write(tmp_path, text)
    with pytest.raises(TargetFileError, match=needle) as e:
        load(p)
    assert str(p) in str(e.value)


def test_the_cli_checks_a_target(tmp_path, capsys):
    assert main(["target", "check", str(EXAMPLE)]) == 0
    assert "valid target definition" in capsys.readouterr().out
    assert main(["target", "check", str(_write(tmp_path, SITE))]) == 2
    assert "schema_version" in capsys.readouterr().err


def test_the_sections_are_the_campaign_configs_own():
    from smeltery.mcp.screening import TARGET_SECTIONS

    assert target_file.SECTIONS == TARGET_SECTIONS
