# Adding a method

A method is declared once, as a `MethodSpec` in the registry (`src/smeltery/methods/__init__.py`). Everything that
used to list methods by hand is derived from `registry()`.

## The registry

`MethodSpec` is a frozen dataclass. `key`, `label`, `rung` and `role` are required; the rest have defaults.

| Field | Meaning |
|---|---|
| `key`, `label` | the method's id and its display name |
| `rung`, `role` | its place in the hierarchy (`smeltery.gui.catalog.RUNGS`, `ROLES`); an unknown one is refused at `register` |
| `step` | which step of the pipeline drawing holds it: one of `active_site`, `ligands`, `poses`, `checks`, `measure`, `cut` |
| `slot` | the execution slot it fills (below), or `None` for a stage whose code lives in `smeltery.cli` |
| `section` | its config table, `[section]` |
| `knobs` | a dataclass whose init fields are the section's keys (a tier's own class, so defaults cannot drift) |
| `params` | typed knobs for a form (`smeltery.gui.catalog.Param`); derived from `knobs` when empty |
| `file_knobs` | knobs that name input files, so staging copies them (each must be among the knobs) |
| `tier` | the tier it runs |
| `required`, `exclusive` | every pipeline needs it; or the keys of methods it cannot be used beside |
| `wired`, `order`, `why_unwired` | `smeltery run` executes a wired method at `order`; an unwired method has no order and states why |
| `hook` | an object implementing the slot's protocol |
| `needs`, `provides` | artefacts (`receptor`, `field`, `parent_poses`, `poses`, `dE_int`, `survivors`) |
| `gate_key` | the `[gates]` key that switches it on |
| `summary`, `extras`, `quantity`, `implicit` | catalog text and metadata |
| `validate` | `validate(cfg)` replaces the generic section check; called for every config, present or not; raises `smeltery.cli.ConfigError` |
| `cost_model`, `memory_model` | callables returning a sentence for `--plan` and the catalog |

`__post_init__` refuses: an unknown slot or step; a hook without a slot, or without a section, or that does not
implement the slot's protocol; a wired method without an order (or an unwired one with one); a file knob that is not a
knob. `register(spec)` additionally refuses a key already registered, a section or gate key already used by another
method, and anything the catalog would refuse. `unregister(key)` removes a registration (a test's dummy method).

### The slots

A hook runs when the method's section is in the config (or its gate is switched on). All protocols are
`runtime_checkable`.

| Slot | Protocol | Gives |
|---|---|---|
| `pose_source` | `parent_poses(cfg, parent, ctx) -> list[Pose]` | the parent's poses; analogues are paired to them |
| `field_source` | `field(cfg, parent, ctx) -> list[PointCharge]` | the pocket's point charges, in the poses' frame |
| `measure` | `tier(cfg) -> tier` | the tier that measures dE_int |
| `analysis` | `run(plan, rec, ctx) -> dict` | runs after the measurement; the dict is merged into `results[key]` |

At most one active method per `pose_source`, `field_source` and `measure` slot is allowed (the config is refused
otherwise); any number of analyses run. A builtin with `hook=None` keeps its own code path in `smeltery.cli`.

An analysis that re-measures under other fields (`--temporal` support) also defines `extra_fields(plan, ctx) ->
{model: [PointCharge]}` and `summarize(plan, rec, measured) -> dict`, and optionally `extra_models(cfg)` to count SCFs
at `--plan`; see [temporal-runs.md](temporal-runs.md) and `[charge_sensitivity]` below.

## Reading what a method accepts

`smeltery methods` prints every registered method as a commented TOML block: its config section, every knob with
its type, default and help, what it needs and provides, which methods it is refused beside, and any extra it needs
installed. The text is derived from the registration, so it cannot drift from the code.

```bash
smeltery methods                                 # all of them, in execution order
smeltery methods --only field_interaction        # one
smeltery methods --out docs/methods/             # index.toml plus one file per method
```

Each block is valid TOML and is meant to be pasted into a pipeline config: a knob that must be set is already
there, one that is optional is a comment. For a whole config built this way, see
[worked-example.md](worked-example.md).

## Discovery

Every module in `smeltery/methods/` (other than `builtin` and names starting `_`) that defines `register_methods()` is
imported and called, in name order after the builtins, the first time `registry()` is used. Adding a method is
adding a module there: there is no list to edit.

## What derives from a registration

- **the catalog** (`smeltery.gui.catalog`): the palette, forms and the `catalog_json` a GUI draws;
- **the execution order** (`execution_order()`: wired methods by `order`, ties by registration) and **the steps** of
  the pipeline drawing (`smeltery.gui.pipeline`, `smeltery.gui.steps`);
- **the CLI's top-level keys and section validation** (`smeltery.cli`): a section is a table whose keys are the
  knobs, and an `exclusive` method's section is refused beside its partner;
- **input staging** (`smeltery.run_inputs`): each `file_knobs` file is copied under `OUT/inputs/`.

**A hook method's section enters the input digest.** For each active method with a hook, its section is added to the
run's inputs, with every file knob replaced by the file's sha256 (so a staged copy digests the same). Changing a knob
changes the digest. Builtins carry no hook, so configs that use only builtins keep their digests.

## A worked example

(For the methods below in a whole running pipeline, see
[worked-example.md](worked-example.md).)

A method that adds a count to the record. It is an `analysis`, so it fills a slot but changes no measurement:

```python
# src/smeltery/methods/pose_tally.py
from __future__ import annotations

from dataclasses import dataclass

from . import MethodSpec, register


@dataclass(frozen=True)
class PoseTallyKnobs:
    per_candidate: bool = True


class PoseTally:
    """The `analysis` hook: runs after the measurement, returns a dict merged into results["pose_tally"]."""

    def run(self, plan, rec, ctx: dict) -> dict:
        k = PoseTallyKnobs(**plan.cfg["pose_tally"])
        n = {c.name: len(c.poses) for c in plan.candidates}
        return {"n_poses": n if k.per_candidate else sum(n.values())}


def register_methods() -> None:
    register(
        MethodSpec(
            "pose_tally",
            "Pose tally",
            "check",
            "recorded",
            step="measure",
            slot="analysis",
            section="pose_tally",
            knobs=PoseTallyKnobs,
            order=160,
            hook=PoseTally(),
            summary="Records how many poses each candidate carries.",
        )
    )
```

Then `[pose_tally]` in a config runs it, and `results.pose_tally` holds the dict. The shipped methods follow the same
shape: `methods/bound_pose.py` (a `pose_source` with `file_knobs`, `exclusive`, `validate` and typed `params`) and
`methods/charge_sensitivity.py` (an `analysis` with `validate` and a re-measuring interface). A knob that names a file must be listed in `file_knobs`, or the file is neither staged nor in the digest. Run the
tests for the files you touch (see `AGENTS.md`).

### Registering from outside the package

Methods have no entry-point group yet (providers do: `smeltery.providers`), so an out-of-tree method cannot be
discovered by installing it. Its module must call `register()` itself, at import time or from a function the host
program calls, and `unregister()` removes it again (what a test does in a finalizer). Discovery of `methods/*.py`
modules is for in-tree methods only.

```python
from __future__ import annotations

from dataclasses import dataclass

from smeltery.methods import MethodSpec, register, unregister


@dataclass(frozen=True)
class EchoKnobs:
    label: str = "hello"
    repeat: int = 2


class Echo:
    """The `analysis` hook: its dict is merged into results["echo"]."""

    def run(self, plan, rec, ctx: dict) -> dict:
        k = EchoKnobs(**plan.cfg["echo"])
        return {"text": k.label * k.repeat}


ECHO = MethodSpec(
    "echo",
    "Echo",
    "check",
    "recorded",
    step="measure",
    slot="analysis",
    section="echo",
    knobs=EchoKnobs,
    order=170,
    hook=Echo(),
    summary="Repeats a label; a third-party method in its smallest form.",
    cost_model=lambda: "negligible: no SCF",
)


def install() -> None:
    register(ECHO)


def uninstall() -> None:
    unregister("echo")
```

After `install()`, `echo` is in the catalog, the execution order, the CLI's top-level keys, `smeltery methods` and the
Bench Methods tab (`GET /api/methods`), where its settings and defaults come from `EchoKnobs` and a workspace default
can be set with `PUT /api/methods/echo`. `tests/bench/test_methods_api.py` runs this block and checks exactly that.

## `[bound_pose]`: the parent's poses from a bound conformer

`methods/bound_pose.py`. A `pose_source` in the `geometry` rung, step `poses`, order 25, `exclusive` with docking
(and refused beside `[structure]`, which docks). It gives the parent's poses from an experimental structure's ligand
instead of ETKDG or docking.

```toml
[bound_pose]
file = "conf_00_cryo_em.xyz"   # required
seed = 20261005                # required
n_poses = 4
jitter_deg = 15.0
jitter_ang = 0.5
skip_first = 0
```

| Knob | Default | Meaning |
|---|---|---|
| `file` | required | XYZ of the parent as bound, in the field's frame, hydrogens included (heavy atoms from the structure, hydrogens placed by any tool). A file knob: staged under `OUT/inputs/` and its sha256 is in the input digest |
| `seed` | required (int >= 0) | seeds the parent's rigid jitters |
| `n_poses` | 6 (int >= 1) | parent poses kept: the bound conformer plus `n_poses - 1` jitters |
| `jitter_deg` | 15.0 (>= 0) | largest random rotation, degrees, about the centroid |
| `jitter_ang` | 0.5 (>= 0) | largest random shift, angstrom |
| `skip_first` | 0 (int >= 0) | leading poses built and then dropped before pairing |

Pose 0 is the conformer, relabelled to the atom order of the parent SMILES and otherwise unaltered; poses 1.. are
seeded rigid jitters of it. The analogues are then paired to these poses by `[poses]`, exactly as to docked ones (the
MCS core copied from parent pose i).

**Two seeds.** `[bound_pose] seed` draws the parent's jitters. `[poses] seed` seeds the analogues' substituent
embeddings. They are independent; the danuglipron example used 20261005 for both.

**`skip_first`.** The hook builds `n_poses + skip_first` poses and drops the first `skip_first`, so `n_poses` are kept
and the dropped ones are never paired, embedded or relaxed. It sets `[poses] n_poses` (overwriting a value there, so
the record states the count paired) and, when `skip_first` is non-zero, `[poses] first_pose_index = skip_first`, so
each kept analogue pose embeds with the seed it had in the undropped ensemble. Setting `first_pose_index` yourself to
something else is refused. `skip_first = 1` drops the bound conformer itself.

## `[charge_sensitivity]`: the measurement repeated under another pocket charge model

`methods/charge_sensitivity.py`. An `analysis` in the `ab_initio` rung, step `measure`, order 150. The same pocket
file is charged again by pdb2pqr with `--ff=<model>` (same file, cutoff, centre and water choice; only the force field
differs), every pose of every candidate is measured again with the plan's own tier in that field, and the
disagreement with the primary measurement is reported. This documents the mechanism; the shipped docs make no claim
about a measured sensitivity.

```toml
[charge_sensitivity]
models = ["CHARMM"]
```

`models` is a non-empty list of distinct pdb2pqr force fields from `AMBER, CHARMM, PARSE, TYL06, PEOEPB, SWANSON`
(default `["CHARMM"]`), excluding the force field the primary pocket is charged with (the default of `load_pocket`).
The pocket must be one pdb2pqr charges: `[pocket] file` ending in `.pdb`, or `[structure]`; point-charge sites and a
`.pqr` carry no force field to swap and are refused. All of this is checked before any SCF runs. It needs pdb2pqr on
PATH.

Interface (the Temporal runner uses the first three; `run` is the serial path):

- `extra_models(cfg)`: the model names, without running pdb2pqr (so `--plan` can count SCFs: poses x candidates x
  (1 + models));
- `extra_fields(plan, ctx)`: `{model: the pocket re-charged}`, each a `PocketField` whose provenance records the force
  field; it raises if the primary field is not a pdb2pqr-charged `.pdb`, the file's digest changed, or pdb2pqr fails;
- `summarize(plan, rec, measured)`: `measured[model][candidate]` is that candidate's per-pose dE_int under the model
  (`None` for a failed or dropped pose). Returns, per model, `paired_ddE` (per analogue: `primary`, `model`, `delta`,
  `n`), `sign_flips`, `spearman`, `floor`, `n`, `dropped_poses`, `parent_dropped_poses`, `n_dropped`, `excluded` and
  `per_pose`. Poses are dropped pairwise: a pose missing on either side (or for the parent on either side) is dropped
  from both. With fewer than two analogues left the entry says why (`note`) instead of giving a statistic;
- `run(plan, rec, ctx)`: per model, its field, then every pose under it serially, then `summarize`. The result is
  merged into `results.charge_sensitivity`.

**Failures are recorded only when expected.** A field that cannot be built for an expected reason (pdb2pqr exiting
non-zero or missing, an unreadable pocket file, or the `ValueError`s `extra_field` raises by design) is recorded as
`{"error": ..., "stage": "field"}` for that model. An SCF that does not converge (`RuntimeError`) is recorded as a
`None` pose with its error under `failures`, and is not retried. Any other exception is a bug and propagates with its
traceback; it is not recorded as a measured outcome.

Its extra SCF lines go to `calcs.jsonl` with `field_model` set to the model name (and their `rung`), and the record
keeps only their count (see [bench.md](bench.md#provenance-and-lineage)). Under `--temporal` the extra field legs
share the vacuum leg with the primary field ([temporal-runs.md](temporal-runs.md)).
