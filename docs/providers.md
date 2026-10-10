# Adding a provider

smeltery has five provider kinds. A third party adds a sixth implementation of any of them
without a PR to smeltery: register it through an entry point, and run the shared conformance
suite against it.

| kind | Protocol | module |
|---|---|---|
| `structure` | `StructureProvider` | `smeltery.providers.structure` |
| `scoring` | `ScoringProvider` | `smeltery.scoring` |
| `docking` | `DockingProvider` | `smeltery.docking.base` |
| `property` | `PropertyProvider` | `smeltery.properties` |
| `potential` | `PotentialProvider` | `smeltery.providers.potential` |

## Registering

One entry-point group, `smeltery.providers`. The entry **name** is `<kind>:<name>`; the entry
**value** is `module:attribute` pointing at a **factory**, a class or any callable that returns a
provider instance. The registry returns factories, never instances: a factory may need arguments
(`VinaScoreProvider(scores)`) or load weights, so the caller decides when to build one.

```toml
[project.entry-points."smeltery.providers"]
"property:ring-count" = "my_pkg.providers:RingCountProvider"
```

```python
from smeltery.providers.registry import discover, load_all

disc = discover()  # reads installed metadata only; imports NO provider module
entry = disc.get("property", "ring-count")
factory = entry.load()  # imports my_pkg here, on request
provider = entry.create()  # calls the factory, checks it is a property provider
report = load_all()  # everything; failures land in report.problems, not exceptions
```

Discovery never imports third-party code. A registration that is malformed, imports with an
error, is not callable, is of the wrong kind, or is claimed by two distributions at once is
reported as a `Problem` (stage `name`, `import`, `factory`, `kind` or `duplicate`) and the rest
still load. smeltery's own reference providers are registered the same way (see `pyproject.toml`).

## Licensing

Every registered provider needs a licence row: add yours to the "Registered providers and their rows" table in
[licensing.md](licensing.md), or `tests/test_licensing.py` fails. That page also lists which tools are excluded
and why, and Chai-1 as the next structure provider (listed, not implemented).

## Conformance

`smeltery.providers.conformance` checks, per kind: a declared SPDX `license_id` (the licence of
the provider's *code*; the licence of the *data* it serves goes in `data_license_id`, wrapped
engines in `engine_licenses`), a declared unit, "cannot answer" that is `None` / an error and
never a made-up number, `settings()` that round-trips through `RunRecord` with the same digest,
and a trivial-limit anchor. The module docstring defines each of these precisely per kind.
Run it from pytest with `assert_conforms(kind, case)`, or one test per check with
`check_names(kind)` and `run_check(kind, name, case)`.

## A complete example (a new property provider, 29 lines)

<!-- example:begin -->
```python
from rdkit import Chem

from smeltery.providers.conformance import PropertyCase, assert_conforms


class RingCountProvider:
    name = "ring-count"
    requires_network = False
    license_id = "MIT"  # SPDX id of THIS code

    def settings(self):
        return {"ring_set": "SSSR"}

    def endpoint_units(self):
        return {"rings": "rings (count)"}

    def endpoint_notes(self):
        return {"rings": "SSSR ring count; rank-only"}

    def applicability(self, smiles):
        return Chem.MolFromSmiles(smiles) is not None

    def predict(self, smiles):
        mol = Chem.MolFromSmiles(smiles)
        return {"rings": None if mol is None else float(mol.GetRingInfo().NumRings())}  # None != 0.0


anchors = [("CCO", {"rings": 0.0}), ("c1ccccc1", {"rings": 1.0})]  # a real 0.0, and a positive control
assert_conforms("property", PropertyCase(RingCountProvider, "not a molecule", anchors))
```
<!-- example:end -->

`tests/test_provider_conformance.py` extracts that block and executes it, so it cannot rot.
Add the `[project.entry-points]` line above to the package's `pyproject.toml` and the provider
is discoverable.
