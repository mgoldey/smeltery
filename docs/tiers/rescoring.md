# rescoring

`smeltery.scoring.Rescoring`. Scores each candidate's existing poses with a `ScoringProvider`, at the docking tier's place in the ladder.

## Computes

- **Quantity:** `rescore`, unit `kcal/mol` for the Vina replay provider; in general the unit is taken from the provider's `Score` objects. The quantity name is a constructor argument. `produces()` raises until the tier has run, because the unit comes from the scores; it does not guess.
- Whether the numbers may be differenced is decided by the provider's `Score.is_delta_g`, recorded after a run. An unrun tier reports `is_delta_g = False`.
- Needs poses already on each candidate; a candidate with none raises `ValueError`. `ctx["receptor"]` is passed to the provider and may be absent.

## Native settings

| key | default |
|---|---|
| `provider` | the provider's `name` (required constructor argument) |
| `provider_settings` | the provider's `settings()` |
| `quantity` | `rescore` |
| `unit` | `None` until a run |
| `is_delta_g` | `None` until a run |
| `explicit_floor` | `None` |

## Cost

- **Cost:** UNMEASURED
- `estimate_cost()` returns `predicted=None`: "no timing recorded for provider". The cost is the provider's, and no provider has been timed in this repository. The only provider shipped, `VinaScoreProvider`, replays scores that already exist.

## Gates

- **G5, not a free energy (applied to this tier's output).** A provider that declares `is_delta_g=False` makes `funnel.paired_delta(..., tier=...)` raise `IncomparableError`. Its ordering may be informative; its differences are not (`tests/test_scoring.py::test_rank_only_provider_cannot_be_differenced_and_message_explains`).
- **G4, unmeasured floor (applied to this tier's output).** The floor is the RMS of the provider's per-pose uncertainties, or an explicit `floor=` argument; with neither it is `None` and `funnel.cut(tier=...)` refuses.
- Its own guards: a non-finite score, a negative or infinite uncertainty, a provider returning the wrong number of scores, or a provider mixing units or `is_delta_g` within a run raises `ValueError`. A run that fails part-way clears its state, so no plausible-looking floor survives it.

## Systematic floor

- **Floor (`rescore`):** UNMEASURED
- The floor depends on the provider and the run: the RMS of the reported per-pose uncertainties is a typical single-pose error that does not shrink with n and does not double between parent and analogue. For the Vina replay provider, which reports no uncertainty, it is `None`. No ML rescorer ships, so no floor of any provider has been measured here.

## Not licensed to claim

- A binding free energy, or a difference of rescores, when the provider is rank-only.
- A ranking finer than the provider's uncertainty; a noisy provider yields tie groups, not an order.
- Anything about a provider other than the one actually used: the tier carries no accuracy claim of its own.

## Evidence

- **Anchor:** none. `tests/test_scoring.py` tests the refusals and the floor arithmetic, not a numerical limit.
