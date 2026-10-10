"""The golden path and its mutation harness (issue #33). Shared by tests/test_golden_path_smoke.py and
tests/test_docking_golden_path.py; not a test module itself (pytest does not collect it).

What lives here
---------------
* `CHECKS`: one check per gate G1..G9 of docs/tiers/index.md, each feeding the gate an input it refuses and
  asserting the refusal, plus `check_happy` (the honest outcome of a real `smeltery run`). A check raises
  AssertionError (or any exception) when it fails.
* `MUTATIONS`: for each gate, how to switch ONLY that gate off. `if_false` recompiles the named function from
  its source with the `if` that guards the refusal turned into `if False`; `noop` replaces the gate function
  by one that does nothing. The patched function is installed under every name that refers to it, so a
  `from .funnel import tier_floor` in cli.py is patched too. A needle that matches no `if`, or two, is an
  error: a mutation that changed nothing would be a false proof.
* The CLI runs as a SUBPROCESS (`run_cli`): PoseBusters cannot run in-process under pytest's stderr capture.
  The subprocess is this file run as a script (`python tests/golden_path.py --mutate G3:0 -- run ...`): it
  applies the mutation, optionally traces which tiers ran and which gates were applied, then calls
  `smeltery.cli.main`, the same entry point `python -m smeltery` uses. No src code carries a test hook.
* `TIER_PLAN`: the reviewed table that accounts for every registered tier (see `tier_accounting`).
"""

from __future__ import annotations
import __future__

import ast
import dataclasses
import importlib
import inspect
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"

# ------------------------------------------------------------------------------ mutation machinery


def _aliases(old) -> list[tuple[object, str]]:
    """Every (module-or-class, attribute) under which `old` is reachable inside smeltery."""
    found = []
    for modname, mod in list(sys.modules.items()):
        if mod is None or not (modname == "smeltery" or modname.startswith("smeltery.")):
            continue
        for attr, val in list(vars(mod).items()):
            if val is old:
                found.append((mod, attr))
    return found


def replace_function(modname: str, qualname: str, new_fn_factory: Callable) -> Callable[[], None]:
    """Install `new_fn_factory(old)` for `modname:qualname` under every name it has; return the undo."""
    module = importlib.import_module(modname)
    parts = qualname.split(".")
    owner = module
    for p in parts[:-1]:
        owner = getattr(owner, p)
    old = getattr(owner, parts[-1])
    new = new_fn_factory(old)
    assert new is not old
    targets = [(owner, parts[-1])] if len(parts) > 1 else _aliases(old)
    assert targets, f"{modname}:{qualname} is not reachable"
    for obj, attr in targets:
        setattr(obj, attr, new)

    def undo() -> None:
        for obj, attr in targets:
            setattr(obj, attr, old)

    return undo


def _if_false_factory(modname: str, qualname: str, needles: tuple[str, ...]) -> Callable:
    """A factory that recompiles `qualname` with each `if` whose test contains a needle turned into `if False`."""

    def factory(old):
        module = importlib.import_module(modname)
        tree = ast.parse(inspect.getsource(module))
        node: ast.AST = tree
        for part in qualname.split("."):
            node = next(
                n
                for n in ast.iter_child_nodes(node)
                if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name == part
            )
        hits = dict.fromkeys(needles, 0)

        class Off(ast.NodeTransformer):
            def visit_If(self, n: ast.If):
                self.generic_visit(n)
                src = ast.unparse(n.test)
                for needle in needles:
                    if needle in src:
                        hits[needle] += 1
                        n.test = ast.Constant(False)
                        break
                return n

        fn = Off().visit(node)
        bad = {k: v for k, v in hits.items() if v != 1}
        assert not bad, f"mutation of {modname}:{qualname} matched the wrong number of `if`s: {bad}"
        fn.decorator_list = []
        mod = ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[]))
        code = compile(mod, inspect.getsourcefile(module), "exec", flags=__future__.annotations.compiler_flag)
        local: dict = {}
        exec(code, module.__dict__, local)  # globals stay the module's own, so a later patch is seen
        return local[fn.name]

    return factory


def _noop(*_args, **_kwargs):
    return None


def _zero_charge_floor(old):
    def charge_sensitivity(*args, **kwargs):
        return dataclasses.replace(old(*args, **kwargs), floor=0.0)

    return charge_sensitivity


#: gate -> the ways to disable it (each variant alone must make the smoke test fail).
MUTATIONS: dict[str, list[Callable[[], Callable[[], None]]]] = {
    "G1": [lambda: replace_function("smeltery.gates", "require_passing_poses", lambda old: _noop)],
    "G2": [
        lambda: replace_function(
            "smeltery.tiers",
            "PairedPoses._paired",
            _if_false_factory(
                "smeltery.tiers",
                "PairedPoses._paired",
                (
                    "mcs.canceled",
                    "not p_idx or not a_idx",
                    "n_heavy < self.min_core_heavy",
                    "len(mapping) != amol.GetNumAtoms()",
                ),
            ),
        )
    ],
    "G3": [lambda: replace_function("smeltery.cli", "_check_parity", lambda old: _noop)],
    "G4": [
        lambda: replace_function(
            "smeltery.funnel", "tier_floor", _if_false_factory("smeltery.funnel", "tier_floor", ("floor is None",))
        )
    ],
    "G5": [
        lambda: replace_function(
            "smeltery.funnel", "paired_delta", _if_false_factory("smeltery.funnel", "paired_delta", ("is_delta_g",))
        )
    ],
    "G6": [
        lambda: replace_function(
            "smeltery.funnel",
            "require_same_formula",
            _if_false_factory("smeltery.funnel", "require_same_formula", ("len(formulas) > 1",)),
        )
    ],
    "G7": [
        lambda: replace_function(
            "smeltery.tiers", "run_checked", _if_false_factory("smeltery.tiers", "run_checked", ("undeclared",))
        )
    ],
    "G8": [lambda: replace_function("smeltery.funnel", "cut", _if_false_factory("smeltery.funnel", "cut", ("wrong",)))],
    "G9": [
        # the consumer: cut() ignores the report ...
        lambda: replace_function(
            "smeltery.funnel", "cut", _if_false_factory("smeltery.funnel", "cut", ("sensitivity is not None",))
        ),
        # ... and the producer: the report claims the models agree everywhere (floor 0.0)
        lambda: replace_function("smeltery.gates", "charge_sensitivity", _zero_charge_floor),
    ],
}

#: Checks that ALSO fail when a gate is off, because the happy path leans on that gate. Reviewed, not discovered.
COLLATERAL: dict[str, set[str]] = {
    # without the unmeasured-floor refusal, tier_floor() returns None instead of raising, so the CLI never falls
    # back to the config's explicit [cut] floor and the honest run itself breaks
    # (check_g1's control run is a --plan of the same config, so it leans on the same fallback)
    "G4": {"happy", "G1"},
}


def apply_mutation(spec: str) -> Callable[[], None]:
    """spec 'G3:0' -> disable variant 0 of G3; return the undo."""
    gate, _, idx = spec.partition(":")
    return MUTATIONS[gate][int(idx or 0)]()


# ------------------------------------------------------------------------------ tracing (which tiers/gates ran)

#: (module, qualname, predicate(args, kwargs) -> gate ids it APPLIES on this call). A gate is "applied" only when
#: the call can refuse: `paired_delta` without a tier cannot raise the G5 refusal, `cut` without `tier=`
#: skips G8, `cut` without `sensitivity=` skips G9.
TRACE_POINTS: list[tuple[str, str, Callable[[tuple, dict], list[str]]]] = [
    ("smeltery.gates", "require_passing_poses", lambda a, k: ["G1"]),
    ("smeltery.tiers", "PairedPoses._paired", lambda a, k: ["G2"]),
    ("smeltery.cli", "_check_parity", lambda a, k: ["G3"]),
    ("smeltery.funnel", "tier_floor", lambda a, k: ["G4"]),
    (
        "smeltery.funnel",
        "paired_delta",
        lambda a, k: ["G5"] if (a[3] if len(a) > 3 else k.get("tier")) is not None else [],
    ),
    ("smeltery.funnel", "require_same_formula", lambda a, k: ["G6"]),
    ("smeltery.tiers", "run_checked", lambda a, k: ["G7"]),
    (
        "smeltery.funnel",
        "cut",
        lambda a, k: (
            (["G8"] if k.get("tier") is not None else []) + (["G9"] if k.get("sensitivity") is not None else [])
        ),
    ),
]


def install_tier_trace(completed: list[str], failed: list[str]) -> Callable[[], None]:
    """Wrap `run` of every registered tier: record its name when it RETURNS (entering and raising is not reaching)."""
    from smeltery.tier_registry import registered_tiers

    undos = []
    for name, cls in registered_tiers().items():

        def make(orig, name=name):
            def run(self, candidates, ctx):
                try:
                    orig(self, candidates, ctx)
                except BaseException:
                    failed.append(name)
                    raise
                completed.append(name)

            return run

        orig = cls.run
        cls.run = make(orig)
        undos.append(lambda cls=cls, orig=orig: setattr(cls, "run", orig))
    return lambda: [u() for u in undos]


def install_gate_trace(applied: dict[str, int]) -> Callable[[], None]:
    undos = []
    for modname, qualname, pred in TRACE_POINTS:

        def make(old, pred=pred):
            def wrapper(*a, **k):
                for g in pred(a, k):
                    applied[g] = applied.get(g, 0) + 1
                return old(*a, **k)

            return wrapper

        undos.append(replace_function(modname, qualname, make))
    return lambda: [u() for u in undos]


# ------------------------------------------------------------------------------ the CLI as a subprocess


def _shim(argv: list[str]) -> int:
    """`python golden_path.py --mutate SPEC --trace FILE -- <smeltery args>`: mutate, trace, call the real main()."""
    sep = argv.index("--")
    opts, cli_args = argv[:sep], argv[sep + 1 :]
    mutate = opts[opts.index("--mutate") + 1]
    trace = opts[opts.index("--trace") + 1]
    if mutate:
        apply_mutation(mutate)
    completed: list[str] = []
    failed: list[str] = []
    applied: dict[str, int] = {}
    if trace:
        install_tier_trace(completed, failed)
        install_gate_trace(applied)
    from smeltery.cli import main

    code = 1
    try:
        code = main(cli_args)
    finally:
        if trace:
            Path(trace).write_text(json.dumps({"completed": completed, "failed": failed, "gates_applied": applied}))
    return code


def run_cli(args, mutate: str | None = None, trace: Path | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "OPENBLAS_NUM_THREADS": "1"}
    cmd = [sys.executable, str(Path(__file__).resolve()), "--mutate", mutate or "", "--trace", str(trace or ""), "--"]
    return subprocess.run([*cmd, *map(str, args)], capture_output=True, text=True, env=env)


# ------------------------------------------------------------------------------ configs


def config(
    *,
    candidates=(("ethanol", "CCO"), ("propanol", "CCCO"), ("butanol", "CCCCO")),
    relax: bool = True,
    gates: str = "",
    floor: float | None = 1000.0,
    docking: str = "",
    pocket: str | None = None,
) -> str:
    """A tiny campaign: ethanol + two alcohols, 2 poses, ONE point charge, STO-3G (the default basis)."""
    if pocket is None:
        pocket = "[[pocket.site]]\nq = 1.0\natom = 2\nfrom_atom = 1\ndistance = 3.0\n"
    # with [docking] the pose count is [docking] n_poses; [poses] n_poses would be a config error
    poses = ("" if docking else "[poses]\nn_poses = 2\n") + ("relax_unmapped = true\n" if relax else "")
    if poses and docking:
        poses = "[poses]\n" + poses
    cands = "\n".join(f'{n} = "{s}"' for n, s in candidates)
    cut = "[cut]\nkeep = 1\nz = 2.0\n" + (f"floor = {floor}\n" if floor is not None else "")
    head = 'campaign = "smoke"\nparent = "ethanol"\n'
    return f"{head}\n[candidates]\n{cands}\n\n{poses}\n{pocket}\n{docking}\n{gates}\n{cut}"


def write_config(tmp: Path, text: str, name: str = "smoke.toml") -> Path:
    p = tmp / name
    p.write_text(text)
    return p


def xtb_available() -> bool:
    return shutil.which("xtb") is not None


def gates_block(*, posebusters=True, forcefield=True, xtb=None) -> str:
    xtb = xtb_available() if xtb is None else xtb
    lines = ["[gates]"]
    lines += [f"posebusters = {str(posebusters).lower()}", f"forcefield = {str(forcefield).lower()}"]
    lines += [f"xtb = {str(xtb).lower()}"] if xtb else []
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------------------ the checks


class Ctx:
    def __init__(self, tmp: Path, mutate: str | None):
        self.tmp, self.mutate = tmp, mutate
        self.n = 0
        self.records: list[dict] = []  # the run records check_happy read back

    def cli(self, text: str, *args, trace: Path | None = None) -> subprocess.CompletedProcess:
        self.n += 1
        cfg = write_config(self.tmp, text, f"c{self.n}.toml")
        return run_cli(["run", cfg, *args], self.mutate, trace)


def _out(r) -> str:
    return f"\n--- rc {r.returncode}\nstdout:\n{r.stdout[-1500:]}\nstderr:\n{r.stderr[-1500:]}"


def _refused(r, rc: int, *needles: str, what: str) -> None:
    assert r.returncode == rc and all(n in r.stderr for n in needles), f"{what}: not refused as required{_out(r)}"


def check_g1(ctx: Ctx) -> None:
    """G1 pose validity: unrelaxed analogue poses fail PoseBusters at the junction -> exit 3, stage named."""
    text = config(relax=False, gates=gates_block(forcefield=False, xtb=False))
    r = ctx.cli(text, "--plan")
    _refused(r, 3, "stage 'posebusters' failed", "propanol", what="G1")
    r = ctx.cli(config(relax=True, gates=gates_block(forcefield=False, xtb=False)), "--plan")  # the control
    assert r.returncode == 0, f"G1 control (relaxed poses) must pass{_out(r)}"


def check_g2(ctx: Ctx) -> None:
    """G2 common core: benzene shares no core with ethanol -> exit 3 from the 'poses' stage."""
    cands = (("ethanol", "CCO"), ("propanol", "CCCO"), ("benzene", "c1ccccc1"))
    r = ctx.cli(config(candidates=cands), "--plan")
    _refused(r, 3, "stage 'poses' failed", "NoCommonCoreError", what="G2")


def check_g3(ctx: Ctx) -> None:
    """G3 electron parity: C[CH]O is a radical (25 electrons) -> exit 3 from the 'parity' stage."""
    cands = (("ethanol", "CCO"), ("propanol", "CCCO"), ("radical", "C[CH]O"))
    r = ctx.cli(config(candidates=cands), "--plan")
    _refused(r, 3, "stage 'parity' failed", "radical", "odd electron count", what="G3")


def check_g4(ctx: Ctx) -> None:
    """G4 unmeasured floor: no [cut] floor while FieldInteraction's floor is None -> exit 2; and cut(tier=) raises."""
    r = ctx.cli(config(floor=None), "--plan")
    _refused(r, 2, "config error", "[cut] floor", "not measured", what="G4 (CLI)")
    from smeltery import Measurement, UnmeasuredFloorError, cut
    from smeltery.tiers import FieldInteraction

    tier = FieldInteraction()
    assert tier.systematic_floor("dE_int") is None, "premise: FieldInteraction's floor is unmeasured"
    m = {"a": Measurement.from_samples([0.0, 0.1, 0.2]), "b": Measurement.from_samples([2.0, 2.1, 2.2])}
    try:
        cut(m, keep=1, tier=tier, quantity="dE_int")
    except UnmeasuredFloorError:
        return
    raise AssertionError("G4 (library): cut(tier=FieldInteraction) did not refuse an unmeasured floor")


def _poses(n=2):
    import numpy as np

    from smeltery import Pose

    return [Pose(("H",), np.zeros((1, 3)))] * n


def check_g5(ctx: Ctx) -> None:
    """G5 not a free energy: a rank-only (is_delta_g=False) score cannot be differenced. Library-only: the CLI
    calls paired_delta without tier= (asserted by check_happy via the gate trace)."""
    from smeltery import Candidate, IncomparableError, Rescoring, Score, VinaScoreProvider, paired_delta

    class DeltaG:
        name = "dg"

        def score(self, poses, receptor=None):
            return [Score(1.0 + i, "kcal/mol", True) for i, _ in enumerate(poses)]

        def settings(self):
            return {}

    def scored(provider):
        cands = [Candidate("parent", "C", _poses()), Candidate("a", "C", _poses())]
        tier = Rescoring(provider)
        tier.run(cands, {})
        return tier, cands

    tier, (parent, a) = scored(DeltaG())  # control: a free energy may be differenced
    assert paired_delta(parent, a, "rescore", tier).mean == 0.0
    tier, (parent, a) = scored(VinaScoreProvider([-5.0, -6.0]))
    try:
        paired_delta(parent, a, "rescore", tier)
    except IncomparableError as e:
        assert "not a free energy" in str(e)
        return
    raise AssertionError("G5: a rank-only Vina score was differenced")


def check_g6(ctx: Ctx) -> None:
    """G6 same formula: ranking a total across differing formulas is refused; isomers pass. Library-only."""
    from smeltery import Candidate, IncomparableError, require_same_formula

    ethanol, propanol, ether = (Candidate(n, s, _fake(s)) for n, s in (("e", "CCO"), ("p", "CCCO"), ("m", "COC")))
    require_same_formula([ethanol, ether], "E")  # control: isomers
    try:
        require_same_formula([ethanol, propanol], "E")
    except IncomparableError:
        return
    raise AssertionError("G6: totals were allowed across C2H6O and C3H8O")


def _fake(smiles):
    """A one-pose candidate's poses from RDKit, enough for `Candidate.formula`."""
    import numpy as np
    from rdkit import Chem

    from smeltery import Pose

    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    return [Pose(tuple(a.GetSymbol() for a in mol.GetAtoms()), np.zeros((mol.GetNumAtoms(), 3)))]


class _Stub:
    """A minimal tier: writes `declared` plus, if `sneaky`, a key it did not declare."""

    name = "stub"

    def __init__(self, sneaky: bool, floor: float | None = 0.5):
        self.sneaky, self._floor = sneaky, floor

    def produces(self):
        return {"q": "kcal/mol"}

    def systematic_floor(self, quantity):
        return self._floor

    def run(self, candidates, ctx):
        for c in candidates:
            c.per_pose["q"] = [0.0]
            if self.sneaky:
                c.per_pose["oops"] = [1.0]


def check_g7(ctx: Ctx) -> None:
    """G7 declared quantity: an undeclared per_pose key is refused by run_checked. Library-only: smeltery run
    calls tier.run() directly."""
    from smeltery import Candidate
    from smeltery.tiers import UndeclaredQuantityError, run_checked

    run_checked(_Stub(False), [Candidate("c", "C", _poses(1))], {})  # control
    try:
        run_checked(_Stub(True), [Candidate("c", "C", _poses(1))], {})
    except UndeclaredQuantityError:
        return
    raise AssertionError("G7: a tier wrote an undeclared per_pose key and run_checked allowed it")


def check_g8(ctx: Ctx) -> None:
    """G8 declared unit: cut(tier=) refuses measurements not in the tier's unit. Library-only."""
    from smeltery import Measurement, cut

    tier = _Stub(False)
    good = {"a": Measurement.from_samples([0.0, 0.1, 0.2]), "b": Measurement.from_samples([2.0, 2.1, 2.2])}
    cut(good, keep=1, tier=tier, quantity="q")  # control
    bad = {k: Measurement.from_samples(list(v.per_pose), "hartree") for k, v in good.items()}
    try:
        cut(bad, keep=1, tier=tier, quantity="q")
    except ValueError as e:
        assert "declared unit" in str(e)
        return
    raise AssertionError("G8: hartree measurements were cut as if they were kcal/mol")


def check_g9(ctx: Ctx) -> None:
    """G9 charge-model sensitivity: two candidates resolved by statistics are NOT ranked once swapping point-charge
    models is shown to move their gap by more than the gap. Library-only (opt-in `cut(sensitivity=)`)."""
    from smeltery import Candidate, Measurement, charge_sensitivity, cut

    m = {"a": Measurement.from_samples([0.0, 0.1, 0.2]), "b": Measurement.from_samples([2.0, 2.1, 2.2])}
    plain = cut(m, keep=1, z=2.0, floor=0.0)
    assert plain.survivors == ["a"] and not plain.unranked_at_boundary, "premise: resolved without the gate"
    cands = [Candidate("a", "C", _poses(1)), Candidate("b", "C", _poses(1))]
    shift = {"a": 0.0, "b": 5.0}  # model B moves b by 5 kcal/mol relative to a: more than their 2.0 gap
    report = charge_sensitivity(cands, "dE", lambda c, q: m[c.name].mean, lambda c, q: m[c.name].mean + shift[c.name])
    assert report.floor == 5.0, f"G9: charge_sensitivity reported floor {report.floor}, expected 5.0"
    gated = cut(m, keep=1, z=2.0, floor=0.0, sensitivity=report)
    assert gated.unranked_at_boundary and gated.survivors is None, "G9: the charge-model floor did not unrank the cut"
    assert gated.floor == 5.0


def check_happy(ctx: Ctx, twice: bool = True, trace: Path | None = None) -> dict:
    """The honest outcome of a real `smeltery run`: UNRANKED with one tie group, every stage's counts, the record,
    the digest (stable across two identical runs, equal to the `--plan` digest, sensitive to the inputs)."""
    gates = gates_block()
    text = config(gates=gates)
    records, printed = [], []
    for i in range(2 if twice else 1):
        out = ctx.tmp / f"out{i}"
        r = ctx.cli(text, "--out", out, trace=trace if i == 0 else None)
        assert r.returncode == 0, f"happy run {i} failed{_out(r)}"
        (path,) = out.glob("run-*.json")
        records.append(json.loads(path.read_text()))
        printed.append(r.stdout)
    ctx.records = records
    rec = records[0]
    assert "UNRANKED" in printed[0] and "tie groups" in printed[0]
    cutr = rec["results"]["cut"]
    assert cutr["unranked"] is True and cutr["survivors"] is None
    assert [sorted(g) for g in cutr["groups"]] == [["butanol", "propanol"]], "one tie group holding both analogues"
    want = [
        ("poses", "pair", 3, 3),
        ("pocket", "field", 3, 3),
        ("parity", "gate", 3, 3),
        ("posebusters", "gate", 3, 3),
        ("forcefield", "recorded", 3, 3),
        *([("xtb", "recorded", 3, 3)] if xtb_available() else []),
        ("field_interaction", "measure", 3, 3),
        ("cut", "rank", 2, None),
    ]
    got = [(r["stage"], r["kind"], r["candidates_in"], r["candidates_out"]) for r in rec["results"]["funnel"]]
    assert got == want, f"funnel counts {got}"
    assert rec["results"]["funnel"][-1]["unranked"] is True
    assert set(rec["results"]["paired_ddE"]) == {"propanol", "butanol"}
    assert all(v["n"] == 2 for v in rec["results"]["paired_ddE"].values())
    assert rec["inputs"]["floor"]["source"].startswith("config [cut] floor")
    names = [t["name"] for t in rec["tiers"]]
    assert names == ["paired_poses", "forcefield", *(["gfn2"] if xtb_available() else []), "field_interaction"], names
    assert rec["input_digest"] in printed[0]
    plan = ctx.cli(text, "--plan")
    assert plan.returncode == 0 and rec["input_digest"] in plan.stdout, "the --plan digest must equal the run digest"
    other = ctx.cli(text.replace("floor = 1000.0", "floor = 999.0"), "--plan")
    assert other.returncode == 0 and rec["input_digest"] not in other.stdout, "the digest must depend on the inputs"
    if twice:
        a, b = records
        assert a["input_digest"] == b["input_digest"], "input_digest is not stable across identical runs"
        assert a["results"] == b["results"], "identical inputs gave different results"
    return rec


CHECKS: dict[str, Callable[[Ctx], None]] = {
    "G1": check_g1,
    "G2": check_g2,
    "G3": check_g3,
    "G4": check_g4,
    "G5": check_g5,
    "G6": check_g6,
    "G7": check_g7,
    "G8": check_g8,
    "G9": check_g9,
}


@dataclass
class Smoke:
    results: dict[str, str | None]  # check name -> failure text, None = passed
    records: list[dict]  # the happy path's run records (two when twice=True)
    cli_trace: dict | None  # what the traced happy CLI run reached: completed/failed tiers, gates applied
    seconds: float

    @property
    def failing(self) -> set[str]:
        return {k for k, v in self.results.items() if v}


def run_smoke(mutate: str | None, *, twice: bool = False, trace: bool = False) -> Smoke:
    """Every check, with `mutate` (e.g. 'G3:0') switched off in BOTH the library (in-process) and the CLI
    (subprocess). One failing check does not stop the others. `twice` runs the happy path twice (the digest
    comparison); `trace` records the tiers and gates the first happy CLI run reached."""
    t0 = time.time()
    undo = apply_mutation(mutate) if mutate else (lambda: None)
    results: dict[str, str | None] = {}
    cli_trace = None
    ctx = None
    try:
        with tempfile.TemporaryDirectory() as d:
            ctx = Ctx(Path(d), mutate)
            tfile = Path(d) / "trace.json" if trace else None
            todo = {"happy": lambda c: check_happy(c, twice=twice, trace=tfile), **CHECKS}
            for name, fn in todo.items():
                try:
                    fn(ctx)
                    results[name] = None
                except Exception as e:  # noqa: BLE001 - a crash is also a failed check
                    results[name] = f"{type(e).__name__}: {e}"[:900]
            if tfile is not None and tfile.exists():
                cli_trace = json.loads(tfile.read_text())
    finally:
        undo()
    return Smoke(results, ctx.records if ctx else [], cli_trace, time.time() - t0)


# ------------------------------------------------------------------------------ tier accounting

TIER_PLAN_DOC = """Every registered tier, how the golden path reaches it, and what the environment must provide.
A tier that is registered but absent from TIER_PLAN fails tests/test_golden_path_smoke.py: add it here WITH a
reach scenario, or declare it unreachable with a reason, in review."""


@dataclass(frozen=True)
class Reach:
    via: str  # "cli" | "library" | "delegated"
    needs: Callable[[], str | None] = lambda: None  # None = available; else the skip reason (stated, not silent)
    note: str = ""


def _ferric_has(attr: str) -> bool:
    import ferric

    return hasattr(ferric, attr)


def _qmmm_engine_gap() -> str | None:
    import ferric

    from smeltery.tiers import _qmmm_api_missing, cut_lj_exclusions_probe

    missing = _qmmm_api_missing(ferric)
    if missing:
        return f"this ferric lacks {missing} (needs ferric >= v0.1.0rc7)"
    ok, lj = cut_lj_exclusions_probe(ferric)
    return None if ok else f"this ferric applies QM-MM LJ across a bonded cut ({lj:.1f} kcal/mol; needs PR #336)"


TIER_PLAN: dict[str, Reach] = {
    "paired_poses": Reach("cli"),
    "field_interaction": Reach("cli"),
    "forcefield": Reach("cli", note="the MMFF path; the openmm path is declared in OPENMM_REASON below"),
    "gfn2": Reach(
        "cli",
        lambda: None if xtb_available() else "no `xtb` binary on PATH (GitHub's runner has none; local runs may)",
    ),
    "docking": Reach(
        "delegated",
        lambda: (
            "docking needs the docking extra and pdb2pqr30, which the `test` job does not install: reached by "
            "tests/test_docking_golden_path.py in CI's `docking` job, where a skip fails the job"
        ),
        note="tests/test_docking_golden_path.py",
    ),
    "rescoring": Reach("library", note="VinaScoreProvider replay; the CLI has no [scoring] section"),
    "surface_esp": Reach(
        "library",
        lambda: (
            None
            if _ferric_has("esp_on_surface")
            else "this ferric has no esp_on_surface (needs mgoldey/ferric#359; the PyPI wheel CI installs lacks it)"
        ),
    ),
    "qmmm": Reach("library", _qmmm_engine_gap, note="tests/data/gly3_ff14sb.json supplies the MM parameters"),
}

#: Sub-paths of a reached tier that the smoke test does NOT exercise, with the reason (reviewed, not silent).
OPENMM_REASON = (
    "ForceField(path='openmm') needs openmm + openmmforcefields + openff-toolkit with AmberTools, a conda-forge "
    "environment that is not installable from PyPI (docs/environments.md); the CLI cannot select it. Covered by "
    "tests/test_forcefield_openmm.py where that environment exists. NOT exercised by the golden path."
)


def tier_accounting(registered, plan, reached) -> list[str]:
    """Problems with the books: a registered tier missing from the plan, a plan entry for no registered tier, or a
    tier whose requirement is met here but which no scenario reached. Empty = every tier accounted for."""
    problems = []
    for name in sorted(set(registered) - set(plan)):
        problems.append(f"registered tier {name!r} is in neither TIER_PLAN's reach scenarios nor its declared gaps")
    for name in sorted(set(plan) - set(registered)):
        problems.append(f"TIER_PLAN names {name!r}, which is not a registered tier")
    for name in sorted(set(registered) & set(plan)):
        r = plan[name]
        if r.via != "delegated" and r.needs() is None and name not in reached:
            problems.append(f"tier {name!r} is available here but no scenario reached it (run() did not return)")
    return problems


# ------------------------------------------------------------------------------ library tier scenarios


def run_library_tiers() -> None:
    """Rescoring, SurfaceEsp, Qmmm through `run_checked` (so they also meet G7) on tiny systems.
    Each runs only if its requirement is met; the reach is measured by the caller's tier trace."""
    import numpy as np

    from smeltery import Candidate, Pose, Rescoring, VinaScoreProvider
    from smeltery.tiers import MmParameters, Qmmm, SurfaceEsp, run_checked

    cands = [Candidate("parent", "C", _poses()), Candidate("a", "C", _poses())]
    tier = Rescoring(VinaScoreProvider([-5.0, -6.0]))
    run_checked(tier, cands, {})
    assert tier.produces() == {"rescore": "kcal/mol"} and tier.is_delta_g is False

    if TIER_PLAN["surface_esp"].needs() is None:
        water = Pose(("O", "H", "H"), np.array([[0.0, 0.0, 0.117], [0.0, 0.757, -0.469], [0.0, -0.757, -0.469]]))
        c = Candidate("water", "O", [water, water])
        run_checked(SurfaceEsp(basis="sto-3g"), [c], {})
        assert all(len(v) == 2 and np.isfinite(v).all() for v in c.per_pose.values())

    if TIER_PLAN["qmmm"].needs() is None:
        params = MmParameters.from_json(DATA / "gly3_ff14sb.json")
        atoms = json.loads((DATA / "gly3_ff14sb.json").read_text())["atoms"]
        names = [f"{a['name']}{a['resid']}" for a in atoms]
        qm = tuple(names.index(n) for n in ("N1", "H1", "H21", "H31", "CA1", "HA21", "HA31"))  # cuts CA1-C1
        xyz = np.array([a["xyz"] for a in atoms], dtype=float)
        c = Candidate("gly3", "NCC(=O)NCC(=O)NCC(=O)O", [Pose(params.symbols, xyz)])
        tier = Qmmm(params, qm, 1)
        run_checked(tier, [c], {})
        assert set(c.per_pose) == set(tier.produces()) and np.isfinite(c.per_pose["E_qmmm"]).all()
        assert tier.cut_bonds(), "the partition must cut a real covalent bond"


if __name__ == "__main__":
    sys.exit(_shim(sys.argv[1:]))
