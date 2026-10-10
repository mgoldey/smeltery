# Tiers

One page per tier: what it computes, its native settings, its measured cost, the gates around it, its systematic floor, and what it is not licensed to claim. A number is on a page only with a source; where nothing is measured the page says `UNMEASURED`.

The tiers are the classes found by `smeltery.tier_registry.registered_tiers()`:

```bash
uv run python -c "from smeltery.tier_registry import registered_tiers; print(list(registered_tiers()))"
```

`tests/test_tier_docs.py` fails if a registered tier has no page here, or a page here names no registered tier. `tests/test_tier_registry.py` fails if a class that satisfies the tier protocol is not registered.

## Evidence

smeltery has no grade scheme of its own. These three facts are the smallest honest summary the repository can derive, and each is re-derived by `tests/test_tier_docs.py`, which fails if this table disagrees with the code or the pages.

- **Anchor:** a named test in this repository checks an exactness or limit property of the tier (a self-pair giving exactly 0.0, an empty field giving the vacuum value, a reproduced reference). It is not an accuracy claim against an independent reference, and a tier whose anchor test needs an optional dependency is anchored only where that dependency is installed.
- **Cost:** measured when the tier's page gives a number with its system, basis and source, UNMEASURED otherwise.
- **Floor:** measured when `systematic_floor()` returns a number for at least one quantity the tier produces, UNMEASURED when it returns `None` for all of them, and `n/a` for a tier that produces no quantity.

| Tier | Anchor | Cost | Floor |
|---|---|---|---|
| [paired_poses](paired_poses.md) | yes | measured | n/a |
| [field_interaction](field_interaction.md) | yes | measured | UNMEASURED |
| [surface_esp](surface_esp.md) | yes | UNMEASURED | UNMEASURED |
| [gfn2](gfn2.md) | yes | measured | measured |
| [forcefield](forcefield.md) | yes | measured | UNMEASURED |
| [docking](docking.md) | yes | UNMEASURED | UNMEASURED |
| [rescoring](rescoring.md) | none | UNMEASURED | UNMEASURED |
| [qmmm](qmmm.md) | yes | UNMEASURED | UNMEASURED |

## Gates

The issue that asked for these pages speaks of six gates per tier. The code does not define a set of six: the refusals below are the ones that exist, each with where it lives. A tier page lists the ones that apply to it and says whether the tier itself applies them or only its output meets them.

| ID | Gate | Where | Refuses |
|---|---|---|---|
| G1 | Pose validity | `src/smeltery/gates.py:require_passing_poses` | A tier run on poses that failed PoseBusters (`PoseGateError`). A candidate with no report passes unless `ctx["require_pose_report"]` is set. |
| G2 | Common core | `src/smeltery/tiers.py:_paired` | A pair with no common core, a core below `min_core_heavy`, a timed-out search, or a partial self-mapping (`NoCommonCoreError`). |
| G3 | Electron parity | `src/smeltery/cli.py:_check_parity` | An odd electron count at the SMILES formal charge. Run by `smeltery run` only, not by any tier. |
| G4 | Unmeasured floor | `src/smeltery/funnel.py:tier_floor` | A cut on a quantity whose tier floor is `None` (`UnmeasuredFloorError`). |
| G5 | Not a free energy | `src/smeltery/funnel.py:paired_delta` | A difference of scores from a tier with `is_delta_g=False` (`IncomparableError`). |
| G6 | Same formula | `src/smeltery/funnel.py:require_same_formula` | Ranking a per-molecule total across differing formulas (`IncomparableError`). Available to callers; neither `cut` nor the CLI calls it. |
| G7 | Declared quantity | `src/smeltery/tiers.py:run_checked` | A tier writing a `per_pose` key it did not declare in `produces()` (`UndeclaredQuantityError`). Applies only to callers that use `run_checked`; `smeltery run` does not. |
| G8 | Declared unit | `src/smeltery/funnel.py:cut` | Measurements not in the tier's declared unit when `cut(tier=...)` is used. |
| G9 | Charge-model sensitivity | `src/smeltery/gates.py:charge_sensitivity` | Nothing: it measures how far swapping point-charge models moves the scores and raises the cut's floor to that (`cut(sensitivity=...)`). Opt-in. |

## Noise that is not a tier floor

`smeltery.pocket.DDE_NOISE_FLOOR_KCAL_MOL` is the noise floor of an unpaired ddE from independent pose ensembles, 4.07 kcal/mol at n = 100 poses on one campaign (see [field_interaction](field_interaction.md)). It is a noise floor of an estimator, not the systematic floor of any tier, and no tier reports it from `systematic_floor()`.

## Rules for these pages

- Current facts only. A page does not narrate what changed; `tests/test_tier_docs.py` fails on revision-history wording.
- A number is quoted with its system, and with its basis where the cost depends on one. The campaign split inverts between STO-3G and def2-svp: docking is 72.7% of the run at STO-3G and 39.8% at def2-svp (`src/smeltery/cost.py`).
- Measurements recorded for these pages are in `measurements.json`, written by `scripts/measure_tier_costs.py`. The site is plain Markdown; no site generator or deployment is configured.

## Adding a tier

Put the class in a module listed in `smeltery.tier_registry.TIER_MODULES` (or add the module there), then add `docs/tiers/<name>.md` with the same sections as the pages above and a row in the table. The tests say which part is missing.
