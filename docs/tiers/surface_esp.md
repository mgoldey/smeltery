# surface_esp

`smeltery.tiers.SurfaceEsp`. A descriptor tier: one RHF per pose, then the electrostatic potential on the pose's van der Waals surface.

## Computes

- **Quantity:** `esp_surface_min`, unit `Eh/e`: the minimum ESP over the retained surface points.
- **Quantity:** `esp_surface_max`, unit `Eh/e`: the maximum.
- **Quantity:** `n_buried`, unit `count`: surface points dropped for lying inside a neighbour atom's scaled vdW sphere.
- The full per-point arrays stay in `SurfaceEsp.surfaces[(candidate name, pose index)]`, for use as features. No pocket field is used: the RHF is the molecule in vacuum.

## Native settings

| key | default |
|---|---|
| `method` | `RHF` |
| `basis` | `sto-3g` |
| `vdw_scale` | `1.4` |
| `n_angular` | `110` |
| `energy_conv` | `1e-10` |
| `density_conv` | `1e-08` |
| `engine` | `ferric` |

## Cost

- **Cost:** UNMEASURED
- `estimate_cost()` returns `predicted=None` ("one RHF plus a surface ESP evaluation per pose; not calibrated"). The RHF part is the same SCF as `field_interaction`'s vacuum calculation, but no timing of the surface evaluation exists, and the ferric wheel on PyPI (0.1.0rc7) has no `esp_on_surface`, so it could not be timed with the CI install.

## Gates

- **G1, pose validity (applied by this tier).** `SurfaceEsp.run` calls `require_passing_poses` on every candidate first.
- Needs a ferric with `esp_on_surface` (mgoldey/ferric#359, commit b22183b). Without it `surface_esp` raises `SurfaceEspUnavailableError` and falls back to nothing (`tests/test_surface_esp.py::test_tier_skips_with_an_actionable_message_on_a_ferric_without_the_binding`).
- Its own guards: SCF non-convergence raises `RuntimeError`; a pose whose every surface point is buried raises `RuntimeError`.
- Like `field_interaction`, it passes no charge or multiplicity to ferric, so every molecule is treated as a neutral singlet and an odd electron count is refused by ferric.

## Systematic floor

- **Floor (`esp_surface_min`):** UNMEASURED
- **Floor (`esp_surface_max`):** UNMEASURED
- **Floor (`n_buried`):** UNMEASURED
- `systematic_floor` returns `None` for all three: "basis / surface-definition sensitivity not yet measured" (`src/smeltery/tiers.py`).

## Not licensed to claim

- A ranking or a binding estimate. It is a descriptor with no floor, so `funnel.cut(tier=...)` refuses it.
- That the ESP extremes are converged in basis: STO-3G is the default and the sensitivity to basis and to `vdw_scale` is unmeasured.
- Any site-level statement: min and max over the whole surface say nothing about where the extreme sits.

## Evidence

- **Anchor:** `tests/test_surface_esp.py::test_surface_esp_reproduces_the_rust_reference_to_1e_10`
