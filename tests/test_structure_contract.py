"""Acceptance tests for smeltery.structure (issue #9), beyond the ported suite.

Each test maps to one acceptance criterion and, where the issue implies one,
carries a negative control showing the check can fail.
"""

from __future__ import annotations

import ast
import builtins
import inspect
import pathlib
import subprocess
import sys
import textwrap
import tomllib

import pytest

from smeltery import structure
from smeltery.structure import MissingBackend, read, read_structure

REPO = pathlib.Path(__file__).resolve().parents[1]

# The same asymmetric water as the ported bitwise test: no zero component, three
# distinct axes. 3 decimals is what PDB can hold; SDF holds 4, so 3-decimal
# values are exact in all three formats.
XYZ = "3\nwater\nO 0.311 0.204 0.117\nH 1.288 0.961 -0.469\nH -0.752 -0.643 -0.288\n"
PDB = textwrap.dedent(
    """\
    ATOM      1  O   HOH A   1       0.311   0.204   0.117  1.00  0.00           O
    ATOM      2  H1  HOH A   1       1.288   0.961  -0.469  1.00  0.00           H
    ATOM      3  H2  HOH A   1      -0.752  -0.643  -0.288  1.00  0.00           H
    END
    """
)


def _sdf(rows, charge_line=""):
    atoms = "\n".join(f"{x:10.4f}{y:10.4f}{z:10.4f} {s:<3} 0  0  0  0  0  0  0  0  0  0  0  0" for s, (x, y, z) in rows)
    return f"water\n  test\n\n{len(rows):3d}  0  0  0  0  0  0  0  0  0999 V2000\n{atoms}\n{charge_line}M  END\n$$$$\n"


GEOM = [("O", (0.311, 0.204, 0.117)), ("H", (1.288, 0.961, -0.469)), ("H", (-0.752, -0.643, -0.288))]


def _w(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return p


# ---- criterion 2: bit-identity across xyz / pdb / sdf --------------------------


def _mols(tmp_path, sdf_text):
    pytest.importorskip("ferric")
    pytest.importorskip("gemmi")
    pytest.importorskip("rdkit")
    return (
        read(_w(tmp_path, "w.xyz", XYZ), 0, 1),
        read(_w(tmp_path, "w.pdb", PDB), 0, 1),
        read(_w(tmp_path, "w.sdf", sdf_text), 0, 1),
    )


@pytest.mark.needs_ferric
def test_xyz_pdb_sdf_give_identical_ferric_coordinates(tmp_path):
    x, p, s = _mols(tmp_path, _sdf(GEOM))
    assert x.symbols() == p.symbols() == s.symbols() == ["O", "H", "H"]
    # `==` on float lists: exact, to 0.0. Approximate equality would pass the
    # very drift this test exists to catch.
    assert x.coords_bohr() == p.coords_bohr() == s.coords_bohr()
    assert all(v != 0.0 for row in x.coords_bohr() for v in row)  # guard the fixture


@pytest.mark.needs_ferric
def test_bit_identity_check_can_fail(tmp_path):
    """Negative control: nudge one SDF coordinate by 1e-4 A (SDF's last digit)."""
    nudged = [("O", (0.3111, 0.204, 0.117))] + GEOM[1:]
    x, _, s = _mols(tmp_path, _sdf(nudged))
    assert x.coords_bohr() != s.coords_bohr()


# ---- criterion 3: charge/multiplicity required, never inferred -----------------


@pytest.mark.parametrize("fn", [structure.read, structure.read_structure, structure.Structure])
def test_charge_and_multiplicity_have_no_default(fn):
    params = inspect.signature(fn).parameters
    for name in ("charge", "multiplicity"):
        assert params[name].default is inspect.Parameter.empty, f"{fn.__name__}.{name} has a default"


def test_omitting_them_is_a_typeerror(tmp_path):
    p = _w(tmp_path, "w.xyz", XYZ)
    with pytest.raises(TypeError):
        read_structure(p)
    with pytest.raises(TypeError):
        read_structure(p, 0)
    with pytest.raises(TypeError):
        read(p, multiplicity=1)


def test_no_inference_from_the_file(tmp_path):
    """A file that states a charge must not change the one the caller passed."""
    pytest.importorskip("rdkit")
    from rdkit import Chem

    sdf = _sdf([("N", (0.0, 0.0, 0.0))], charge_line="M  CHG  1   1   1\n")
    path = _w(tmp_path, "n.sdf", sdf)
    mol = next(iter(Chem.SDMolSupplier(str(path), removeHs=False, sanitize=False)))
    assert Chem.GetFormalCharge(mol) == 1, "premise: the file does state +1"
    assert read_structure(path, 0, 1).charge == 0
    assert read_structure(path, 3, 1).charge == 3


@pytest.mark.needs_ferric
def test_only_from_smiles_may_default_charge():
    sig = inspect.signature(structure.from_smiles).parameters
    assert sig["charge"].default is None
    pytest.importorskip("rdkit")
    pytest.importorskip("ferric")
    # the formal charge in the string is read, and an explicit value overrides it
    assert structure.from_smiles("[NH4+]").nelec() == 10
    assert structure.from_smiles("N", charge=0).nelec() == 10
    # multiplicity is still never inferred: a radical at the default is refused
    with pytest.raises(Exception, match="(?i)multiplicit"):
        structure.from_smiles("[CH3]")


# ---- criterion 4: optional backends, MissingBackend names the extra ------------


def _block(monkeypatch, *names):
    real = builtins.__import__

    def fake(name, *a, **kw):
        if name.split(".")[0] in names:
            raise ImportError(f"blocked {name}")
        return real(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake)


def _extras():
    return tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["optional-dependencies"]


def test_gemmi_and_rdkit_are_declared_extras():
    extras = _extras()
    assert any(r.startswith("gemmi") for r in extras["gemmi"])
    assert any(r.startswith("rdkit") for r in extras["rdkit"])


@pytest.mark.parametrize(
    "suffix, text, module",
    [(".pdb", PDB, "gemmi"), (".sdf", _sdf(GEOM), "rdkit"), (".mol2", "x", "rdkit")],
)
def test_missing_backend_names_the_extra(monkeypatch, tmp_path, suffix, text, module):
    p = _w(tmp_path, "w" + suffix, text)
    _block(monkeypatch, module)
    with pytest.raises(MissingBackend) as ei:
        read_structure(p, 0, 1)
    assert f"smeltery[{module}]" in str(ei.value)
    assert ei.value.extra == module
    assert ei.value.extra in _extras(), "the named extra must exist in pyproject"


def test_missing_rdkit_names_the_extra_for_smiles(monkeypatch):
    _block(monkeypatch, "rdkit")
    with pytest.raises(MissingBackend, match=r"smeltery\[rdkit\]"):
        structure.from_smiles("O")


def test_missing_backend_check_can_fail(monkeypatch, tmp_path):
    """Negative control: blocking the OTHER backend must not raise."""
    pytest.importorskip("gemmi")
    p = _w(tmp_path, "w.pdb", PDB)
    _block(monkeypatch, "rdkit")
    assert read_structure(p, 0, 1).symbols == ("O", "H", "H")


def test_builtin_formats_need_no_backend(monkeypatch, tmp_path):
    _block(monkeypatch, "gemmi", "rdkit")
    assert read_structure(_w(tmp_path, "w.xyz", XYZ), 0, 1).symbols == ("O", "H", "H")


# ---- lazy ferric, no unmigrated dependency -------------------------------------


def test_import_does_not_need_ferric_or_backends(tmp_path):
    p = _w(tmp_path, "w.xyz", XYZ)
    code = (
        "import sys\n"
        "for m in ('ferric', 'gemmi', 'rdkit'):\n"
        "    sys.modules[m] = None\n"
        "from smeltery.structure import read_structure\n"
        f"print(read_structure({str(p)!r}, 0, 1).symbols)\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "('O', 'H', 'H')" in r.stdout


def test_to_molecule_is_where_ferric_is_needed(monkeypatch, tmp_path):
    """Negative control for the lazy import: with ferric blocked, to_molecule fails."""
    st = read_structure(_w(tmp_path, "w.xyz", XYZ), 0, 1)
    _block(monkeypatch, "ferric")
    with pytest.raises(ImportError):
        st.to_molecule()


def test_structure_does_not_import_the_unmigrated_tools_package():
    tree = ast.parse((REPO / "src/smeltery/structure.py").read_text())
    for n in ast.walk(tree):
        mods = [n.module] if isinstance(n, ast.ImportFrom) and n.module else []
        if isinstance(n, ast.Import):
            mods = [a.name for a in n.names]
        assert not [m for m in mods if m.split(".")[0] == "tools"], f"imports tools: {mods}"


def test_pqr_reads_angstrom_exactly(tmp_path):
    """No Bohr round trip: the coordinates come back as written."""
    pqr = "ATOM      1  O   HOH     1       0.311   0.204   0.117  -0.8340 1.7683\n"
    st = read_structure(_w(tmp_path, "w.pqr", pqr), 0, 1)
    assert st.coords == ((0.311, 0.204, 0.117),)


def test_pqr_with_wrong_field_count_is_refused(tmp_path):
    with pytest.raises(structure.StructureError, match="field count"):
        read_structure(_w(tmp_path, "w.pqr", "ATOM 1 O HOH 1 0.0 0.0 0.0 0.0\n"), 0, 1)
