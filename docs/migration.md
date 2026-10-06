# Migrating ferric's `tools/` into smeltery

Written for issue #8 (M2-01). Enforced by `tests/test_migration_rules.py`.

## Rules

1. ferric imports nothing above it. (Delivered by M2-06 / #13, which deletes
   `tools/` from ferric.)
2. Every consumer declares ferric explicitly in `pyproject.toml`: a version
   specifier in `dependencies`, and an exact git `rev` (or an explicit path)
   in `[tool.uv.sources]`. Never an implicit path.
3. No symlinks anywhere in the repo.
4. Library code (`src/smeltery/`) must not import an `experiments` package and
   must not name a specific campaign target in executable code. Prose
   (docstrings, comments) may cite campaign measurements as provenance; the
   test checks imports and executable code only, as ferric's
   `tools/tests/test_library_experiment_boundary.py` does.

## The nine `tools/` packages

"Nine" is counted at the pinned ferric commit `8637a5d`
(`ls tools/` there: active_site, campaign, docking, isomers, morph, pipeline,
structure, tox, viz, plus `tests/`, which is the boundary test and not a
package). A current ferric checkout has no `viz/`, so it lists eight.

Order is derived from the `Depends on:` lines of the migration issues and from
the real `from tools.X import` edges in ferric at the pin. Where an issue
states a dependency it is quoted; where no migration issue covers a package
that is said explicitly rather than guessed.

| Order | Package | Destination | Issue | Stated dependencies |
|-------|---------|-------------|-------|---------------------|
| 0 | (this plan and its tests) | `docs/`, `tests/` | #8 M2-01 | none |
| 1 | `structure` | `smeltery.structure` | #9 M2-02 | #8 |
| 1 | `morph` | `smeltery.generate` | #12 M2-05 | #8 |
| 1 | `isomers` | `smeltery.generate` | #12 M2-05 | #8 |
| 2 | `docking` | `smeltery.docking` | #10 M2-03 | #8, #9 |
| 2 | `active_site` | `smeltery.pocket` | #11 M2-04 | #8, #3 (M1-03) |
| 3 | `pipeline` | partly ported already: `cost.py` (#7), `funnel.py`, `tiers.py`; `substitution.py` goes with #12 | #7, #12 | no dedicated migration issue |
| 3 | `campaign` | xtb engine -> tier 3 (#5); the rest is campaign data | no migration issue | not stated |
| 3 | `tox` | provider interface (#24) | no migration issue | not stated |
| 3 | `viz` | not assigned | none | not stated |
| 4 | removal of `tools/` from ferric | ferric | #13 M2-06 | #9, #10, #11, #12 |

Parallelism: #9 and #12 depend only on #8 and can run together. #10 waits for
#9. #11 waits for #3 (from M1) and #8 only, so the issues allow it to run
alongside #9 and #10. #13 must come after all four.

Gaps the issues do not settle, flagged for the maintainer:

- `campaign`, `tox`, `viz` and the remainder of `pipeline` have no M2
  migration issue. Their "Destination" cells above are inferred from issue
  titles (#5, #24, #7), not from stated dependencies. #13 deletes all of
  `tools/`, so each needs a decision (move or drop) before #13. Order 3 for
  them is a placeholder: it follows the import edges below (`pipeline` imports
  the others) and nothing more.
- Cross-package imports at the pin that constrain order beyond the issues:
  `structure` imports `active_site.pqr_parser` (lazily);
  `campaign/fit.py` and `campaign/strain.py` import `active_site`;
  `pipeline` imports `campaign`, `isomers`, `docking`, `structure`, `tox`.
  #9 therefore needs either `pqr_parser` moved with it or a reader of its own;
  the issue does not mention this.
- `mm_topology` (#11), DECIDED: dropped from smeltery, no re-export; it
  returns to ferric. It is the only `active_site` module importing OpenMM, it
  only assigns AMBER parameters for `ferric.MmTopology.from_amber_units`, and
  it exists to generate ferric-mm validation references (with ferric's
  `scripts/gen_openmm_mm_refs.py`), which is ferric's job. Nothing ported to
  smeltery calls it: `smeltery.pocket.relax_pose_in_pocket` takes an already
  built `ferric.MmTopology`. A re-export would force OpenMM into smeltery (as
  an extra at best) for no smeltery code path, so OpenMM is not a dependency at
  all. Consequence for #13: ferric must hold `mm_topology.py` (and its test)
  before `tools/` is deleted; copying it into ferric is ferric-side work this
  issue does not do. `solvate.py` also lives in `tools/active_site` and is not
  in #11's list; it is not ported here and needs the same move-or-drop decision
  before #13.
- #11 claims `compute_binding_energy` on the 7LCJ fixture gives delta_e
  -17.41 kcal/mol. Measured with the ported code (ferric 8637a5d, def2-svp RHF,
  `conf_00_cryo_em.xyz` + `7LCJ_pocket.pdb`, 6458 charges): -28.758. The only
  -17.41 in the pinned ferric tree is a synthetic number in a viz test. The
  figure is NOT reproduced; the heavy test pins the measured value instead.
  Parser overlap with #9: `smeltery.pocket.loader.parse_pqr` (Angstrom,
  last-5-columns) vs the `structure` branch's own parser; reconcile to one.
- #11 pocket loader: `smeltery.pocket` became a package. `loader.py` (from #3)
  is the single PQR reader; ferric's `pqr_parser.py` / `pocket_charges.py` are
  superseded by it (Angstrom `PointCharge`s rather than Bohr tuples; residue
  ids/atom names are not carried, nothing in #11's scope used them).

## Pinned ferric symbols

The contract is the set of ferric names smeltery code and its ported tests may
depend on. A test asserts each appears in this document.

Runtime (12):

- `Molecule`
- `BasisSet`
- `run_rhf`
- `run_dft`
- `run_optimize`
- `run_optimize_qmmm`
- `QmmmSystem`
- `MmTopology`
- `ConformerEnsemble`
- `hirshfeld_charges`
- `hirshfeld_polarizability`
- `lowdin_charges`

Test-only (2):

- `run_saddle`
- `run_irc`

### What was verified versus what the issue claims

- Issue #8 says "13 symbols", then lists 12 plus 2 test-only (14), then says
  "13-15 of 103". Verified: 12 + 2 = 14. No reading gives 13.
- ferric has no contract test or document that pins these names (searched the
  pinned checkout and current ferric for "contract": the only API-contract
  style file is `tools/tox/tests/test_provider_contract.py`, about toxicity
  providers). What was verified instead: at the pin, a grep of `ferric.<name>`
  across all of `tools/` finds exactly these 14 names; `run_saddle` and
  `run_irc` appear only in tests
  (`tools/pipeline/tests/test_whole_chain_smoke.py`,
  `tools/viz/tests/test_catalyst_plot_chain.py`). The 14 are observed usage,
  not a guarantee ferric publishes.
- Issue says 103 exported symbols; the ferric installed from the pin exports
  105 public names (`len(ferric.__all__)`). All 14 pinned names are present.
- Not re-verified: the issue's claims that `~/qc/ferric-mainline` has no
  symlinks and no crate imports `tools/`. That checkout was not inspected;
  smeltery's symlink test covers smeltery only.
