"""The conformance suite (#25): reference providers pass it, and deliberately broken ones fail it.

The Vina docking provider's engine checks live in `test_docking_conformance.py` (they need the
`docking` extra). Here its static checks (licence, unit, settings) run with `engine=False`.
"""

from __future__ import annotations

import json
import re
import urllib.error
from pathlib import Path

import numpy as np
import pytest
from test_potential_provider import MINIMUM, START, SYMBOLS, Harmonic

from smeltery.model import Pose
from smeltery.providers import AfdbProvider, PdbProvider, PotentialResult
from smeltery.providers.conformance import (
    FAIL,
    SKIP,
    PotentialCase,
    PropertyCase,
    ScoringCase,
    StructureCase,
    assert_conforms,
    check_names,
    is_spdx_expression,
    run_check,
    run_conformance,
)
from smeltery.scoring import VinaScoreProvider

DATA = Path(__file__).parent / "data"
DOCS = Path(__file__).parent.parent / "docs" / "providers.md"


def fails(kind, case, check):
    """The named check, run on `case`, must fail; returns the message."""
    with pytest.raises(AssertionError) as e:
        run_check(kind, check, case)
    return str(e.value)


# ------------------------------------------------------------------ spdx


@pytest.mark.parametrize(
    "text,ok",
    [
        ("MIT", True),
        ("MIT OR Apache-2.0", True),
        ("(MIT OR Apache-2.0) AND BSD-3-Clause", True),
        ("GPL-2.0-or-later WITH Classpath-exception-2.0", True),
        ("CC-BY-4.0", True),
        ("LicenseRef-Proprietary", True),
        ("", False),
        ("MIT OR", False),
        ("(MIT", False),
        ("MIT Apache-2.0", False),
        ("NONE", False),
        ("NOASSERTION", False),
        ("MIT AND AND BSD-3-Clause", False),
        (None, False),
        (3, False),
    ],
)
def test_spdx_syntax(text, ok):
    assert is_spdx_expression(text) is ok


# ------------------------------------------------------------------ structure

AFDB_PDB = (DATA / "afdb_tiny.pdb").read_text()
ENTRY = [{"pdbUrl": "https://example.test/AF-P00000-F1-model_v4.pdb", "latestVersion": 4, "entryId": "AF-P00000-F1"}]


def afdb_fetch(url: str) -> bytes:
    if "api/prediction/P69905" in url:
        return json.dumps(ENTRY).encode()
    if url == ENTRY[0]["pdbUrl"]:
        return AFDB_PDB.encode()
    raise urllib.error.URLError(f"offline fixture has no {url}")


def _pdb_anchor(res):
    assert res.text == (DATA / "gly3.pdb").read_text(), "a local file must come back byte for byte"
    assert res.kind == "EXPERIMENTAL" and res.plddt is None


def _afdb_anchor(res):
    assert res.plddt == ((1, 91.2), (2, 55.5), (3, 38.0))  # the fixture's B-factors
    assert res.text == AFDB_PDB


PDB_CASE = StructureCase(
    lambda: PdbProvider(fetch=afdb_fetch), str(DATA / "gly3.pdb"), "zzz", "EXPERIMENTAL", _pdb_anchor
)
AFDB_CASE = StructureCase(
    lambda: AfdbProvider(fetch=afdb_fetch), "P69905", "not-an-accession", "PREDICTED", _afdb_anchor
)


@pytest.mark.parametrize("check", check_names("structure"))
@pytest.mark.parametrize("case", [PDB_CASE, AFDB_CASE], ids=["pdb", "afdb"])
def test_reference_structure_providers_conform(case, check):
    run_check("structure", check, case)


def test_structure_licences_distinguish_code_from_data():
    assert PdbProvider.license_id == "MIT OR Apache-2.0" and PdbProvider.data_license_id == "CC0-1.0"
    assert AfdbProvider.license_id == "MIT OR Apache-2.0" and AfdbProvider.data_license_id == "CC-BY-4.0"


def test_a_structure_provider_that_fabricates_or_hides_why_fails():
    class Fabricates(PdbProvider):
        def predict(self, spec):  # answers anything, with made-up coordinates
            return super().predict(str(DATA / "gly3.pdb"))

    class Silent(PdbProvider):
        def predict(self, spec):
            super().predict(spec)
            self.last_error = None
            return None

    class NoLicence(PdbProvider):
        license_id = "MIT AND"

    class WrongLabel(AfdbProvider):
        data_license_id = "CC0-1.0"  # result says CC-BY-4.0

    def case(cls, base=PDB_CASE):
        return StructureCase(
            lambda: cls(fetch=afdb_fetch), base.answerable, base.unanswerable, base.expected_kind, base.anchor
        )

    assert "returned" in fails("structure", case(Fabricates), "none_not_fabricated")
    assert "last_error" in fails("structure", case(Silent), "none_not_fabricated")
    assert "SPDX" in fails("structure", case(NoLicence), "license")
    assert "data_license_id" in fails("structure", case(WrongLabel, AFDB_CASE), "labels_and_provenance")

    def reject(res):
        raise AssertionError("anchor says no")

    bad_anchor = StructureCase(PDB_CASE.make, PDB_CASE.answerable, "zzz", "EXPERIMENTAL", reject)
    assert "anchor says no" in fails("structure", bad_anchor, "anchor")


# ------------------------------------------------------------------ property

from smeltery.properties import RdkitAlertProvider  # noqa: E402

RHODANINE = "O=C1C(=Cc2ccccc2)SC(=S)N1"  # benzylidene rhodanine: a PAINS ene_rhod
ALERTS = RdkitAlertProvider
PROPERTY_CASE = PropertyCase(
    RdkitAlertProvider,
    "not a molecule",
    [
        ("CCO", {"pains_alerts": 0.0, "brenk_alerts": 0.0, "nih_alerts": 0.0}),  # no alerts: a real 0.0
        (RHODANINE, {"pains_alerts": lambda v: v >= 1.0, "brenk_alerts": lambda v: v >= 1.0}),  # positive control
    ],
)


@pytest.mark.parametrize("check", check_names("property"))
def test_rdkit_alert_provider_conforms(check):
    run_check("property", check, PROPERTY_CASE)


def test_property_providers_that_fabricate_fail():
    class Zeroes(RdkitAlertProvider):
        def predict(self, smiles):
            return {k: 0.0 for k in self._CATALOGS}  # "no liability" for a molecule it cannot read

    class Constant(RdkitAlertProvider):
        def predict(self, smiles):
            return {k: 0.0 for k in self._CATALOGS}

    class NoUnits(RdkitAlertProvider):
        endpoint_units = None

    class NotRankOnly(RdkitAlertProvider):
        def endpoint_notes(self):
            return {k: "probability of toxicity" for k in self._CATALOGS}

    def case(cls):
        return PropertyCase(cls, PROPERTY_CASE.unanswerable, PROPERTY_CASE.anchors)

    assert "fabricated" in fails("property", case(Zeroes), "none_not_fabricated")
    assert "documented" in fails("property", case(Constant), "anchor") or "predicate" in fails(
        "property", case(Constant), "anchor"
    )
    assert "endpoint_units" in fails("property", case(NoUnits), "units_and_notes")
    assert "rank-only" in fails("property", case(NotRankOnly), "units_and_notes")


# ------------------------------------------------------------------ scoring


def poses(n):
    return [Pose(("C",), np.zeros((1, 3)) + i) for i in range(n)]


SCORING_CASE = ScoringCase(
    lambda: VinaScoreProvider([-7.5, -6.25]), poses(2), [-7.5, -6.25], unanswerable_poses=poses(3)
)


@pytest.mark.parametrize("check", check_names("scoring"))
def test_vina_score_provider_conforms(check):
    run_check("scoring", check, SCORING_CASE)


def test_scoring_providers_that_fabricate_or_mislabel_fail():
    class Pads(VinaScoreProvider):
        def score(self, poses, receptor=None):  # never refuses: invents a score for poses it was not given
            real = super().score(poses[: len(self._scores)], receptor)
            return real + [real[0]] * (len(poses) - len(real))

    class Unitless(VinaScoreProvider):
        def score(self, poses, receptor=None):
            from smeltery.scoring import Score

            return [Score(s, "", False) for s in self._scores]

    class Drifts(VinaScoreProvider):
        calls = 0

        def score(self, poses, receptor=None):
            type(self).calls += 1
            return [
                s.__class__(s.value + type(self).calls * 1e-3, s.unit, s.is_delta_g)
                for s in super().score(poses, receptor)
            ]

    def case(cls, expected=SCORING_CASE.expected):
        return ScoringCase(lambda: cls([-7.5, -6.25]), poses(2), expected, poses(3))

    assert "instead of raising" in fails("scoring", case(Pads), "none_not_fabricated")
    assert "unit" in fails("scoring", case(Unitless), "unit")
    assert "documented" in fails("scoring", case(VinaScoreProvider, [-7.5, -6.0]), "anchor")
    Drifts.calls = 0
    case_d = ScoringCase(lambda: Drifts([-7.5, -6.25]), poses(2), [-7.5, -6.25], poses(3), expected_tol=1.0)
    assert "second identical call" in fails("scoring", case_d, "anchor")


# ------------------------------------------------------------------ settings / RunRecord


def test_settings_that_do_not_round_trip_fail():
    import numpy

    class Tuple(VinaScoreProvider):
        def settings(self):
            return {"shape": (1, 2)}  # a tuple comes back as a list

    class Numpy(VinaScoreProvider):
        def settings(self):
            return {"k": numpy.int64(1)}  # RunRecord's json.dumps(default=str) would hide this one as "1"

    class Nan(VinaScoreProvider):
        def settings(self):
            return {"k": float("nan")}

    class Moves(VinaScoreProvider):
        n = 0

        def settings(self):
            type(self).n += 1
            return {"k": type(self).n}

    def case(cls):
        return ScoringCase(lambda: cls([-7.5, -6.25]), poses(2), [-7.5, -6.25], poses(3))

    for cls in (Tuple, Numpy, Nan, Moves):
        msg = fails("scoring", case(cls), "settings_round_trip")
        assert "settings()" in msg, (cls, msg)


# ------------------------------------------------------------------ potential


class Conforming(Harmonic):
    energy_unit = "kcal/mol"
    license_id = "MIT OR Apache-2.0"

    def __init__(self, **kw):
        super().__init__(**kw)
        self.name = "harmonic-test"


def potential_case(make=Conforming, **kw):
    # Harmonic is anchored to absolute coordinates, so it is NOT translation invariant: say so, use its known minimum.
    return PotentialCase(make, Pose(SYMBOLS, START), minimum=Pose(SYMBOLS, MINIMUM), translation_invariant=False, **kw)


@pytest.mark.parametrize("check", check_names("potential"))
@pytest.mark.parametrize("sees", [True, False])
def test_harmonic_test_potential_conforms(check, sees):
    run_check("potential", check, potential_case(lambda: Conforming(sees_charges=sees)))


def test_potential_without_a_declared_anchor_is_rejected():
    case = PotentialCase(Conforming, Pose(SYMBOLS, START), translation_invariant=False)
    assert "no anchor" in fails("potential", case, "anchor")


def test_potential_translation_invariance_anchor_can_fail_and_pass():
    class Pairwise(Conforming):
        """E = 1/2 k sum_{i<j} (r_ij - r0_ij)^2: depends on distances only, so translation invariant."""

        def energy_and_forces(self, pose, point_charges=None, charge=0):
            x = np.asarray(pose.coords_ang)
            n = len(x)
            e, f = 0.0, np.zeros_like(x)
            for i in range(n):
                for j in range(i + 1, n):
                    v = x[i] - x[j]
                    r = float(np.linalg.norm(v))
                    r0 = float(np.linalg.norm(MINIMUM[i] - MINIMUM[j]))
                    e += 0.5 * self.k * (r - r0) ** 2
                    g = self.k * (r - r0) * v / r
                    f[i] -= g
                    f[j] += g
            return PotentialResult(e, f)

    run_check("potential", "anchor", PotentialCase(Pairwise, Pose(SYMBOLS, START)))
    run_check("potential", "forces_match_finite_difference", PotentialCase(Pairwise, Pose(SYMBOLS, START)))
    # The absolute-coordinate harmonic is NOT translation invariant: claiming so must fail.
    assert "translation" in fails("potential", PotentialCase(Conforming, Pose(SYMBOLS, START)), "anchor")


def test_potentials_that_answer_what_they_must_refuse_fail():
    class AnswersWrongCharge(Conforming):
        supported_charge_states = frozenset({0})

        def energy_and_forces(self, pose, point_charges=None, charge=0):
            return super().energy_and_forces(pose, point_charges, 0)

    class WrongForces(Conforming):
        def energy_and_forces(self, pose, point_charges=None, charge=0):
            r = super().energy_and_forces(pose, point_charges, charge)
            return PotentialResult(r.energy, r.forces * 1.03)

    class WrongMinimum(Conforming):
        def energy_and_forces(self, pose, point_charges=None, charge=0):
            d = np.asarray(pose.coords_ang) - MINIMUM - 0.01
            return PotentialResult(0.5 * self.k * float((d * d).sum()), -self.k * d)

    class Hartree(Conforming):
        energy_unit = "hartree"

    class Mutates(Conforming):
        def energy_and_forces(self, pose, point_charges=None, charge=0):
            r = super().energy_and_forces(pose, point_charges, charge)
            pose.coords_ang[0, 0] += 1e-3  # writes into the caller's array
            return r

    assert "forces disagree" in fails("potential", potential_case(WrongForces), "forces_match_finite_difference")
    assert "from the known minimum" in fails(
        "potential", potential_case(WrongMinimum), "anchor"
    ) or "forces at" in fails("potential", potential_case(WrongMinimum), "anchor")
    assert "kcal/mol" in fails("potential", potential_case(Hartree), "unit")

    assert "modified the input pose" in fails("potential", potential_case(Mutates), "relax_leaves_input_alone")

    class NoRefusal(Conforming):
        """Flags say charge 0 only but the supported set is mutated to include everything."""

        class _All(frozenset):
            def __contains__(self, item):
                return True

        def __init__(self, **kw):
            super().__init__(**kw)
            self.supported_charge_states = self._All({0})

    assert "not refused" in fails("potential", potential_case(NoRefusal), "none_not_fabricated")


# ------------------------------------------------------------------ driver


def test_run_conformance_reports_failures_and_skips_without_raising():
    class Bad(RdkitAlertProvider):
        license_id = "not a licence at all ("

    res = run_conformance("property", PropertyCase(Bad, PROPERTY_CASE.unanswerable, PROPERTY_CASE.anchors))
    assert {r.name: r.status for r in res}["license"] == FAIL
    with pytest.raises(AssertionError, match="license"):
        assert_conforms("property", PropertyCase(Bad, PROPERTY_CASE.unanswerable, PROPERTY_CASE.anchors))
    with pytest.raises(ValueError):
        check_names("nope")
    with pytest.raises(KeyError):
        run_check("property", "nope", PROPERTY_CASE)


def test_vina_provider_static_checks_run_without_the_engine_and_engine_checks_report_skip():
    from rdkit import Chem

    from smeltery.docking import Box, VinaProvider
    from smeltery.providers.conformance import DockingCase

    case = DockingCase(VinaProvider, Chem.AddHs(Chem.MolFromSmiles("CCO")), "x.pdbqt", Box((0, 0, 0)), "/no/such.pdbqt")
    res = run_conformance("docking", case, engine=False)
    status = {r.name: r.status for r in res}
    assert status["license"] == status["unit"] == status["settings_round_trip"] == "PASS"
    assert status["anchor"] == status["none_not_fabricated"] == SKIP


# ------------------------------------------------------------------ docs


def test_docs_example_runs_and_is_at_most_30_lines():
    text = DOCS.read_text()
    m = re.search(r"<!-- example:begin -->\n```python\n(.*?)```\n<!-- example:end -->", text, re.S)
    assert m, "docs/providers.md lost its example markers"
    code = m.group(1)
    n = len(code.rstrip("\n").splitlines())
    assert n <= 30, f"example is {n} lines"
    assert f"{n} lines)" in text, f"heading must state the real line count ({n})"
    ns: dict = {}
    exec(compile(code, "docs/providers.md", "exec"), ns)  # raises if the provider fails conformance
    assert "RingCountProvider" in ns
    # ... and the example must be able to fail: break it and the same call rejects it.
    broken = code.replace("float(mol.GetRingInfo().NumRings())", "0.0")
    assert broken != code
    with pytest.raises(AssertionError, match="documented"):
        exec(compile(broken, "docs/providers.md", "exec"), {})
