# A bond-type head for ScaffoldGrowth: what was tried, and why it was the wrong target

A record of work done 2026-10-17/19 on DiffSBDD's scaffold-growth generator. The result is negative: the bond
head trained successfully and did not improve yield, because bond perception was never what yield depended on.
This is written down so the next person does not re-run it.

None of this code is in smeltery. It lives in `/home/matt/qc/external/sbdd-pool` (model, training, generation)
and `/home/matt/qc/external/sbdd-study` (the 15-cell yield study). Paths are listed at the end.

## The premise, and what was wrong with it

DiffSBDD generates atoms and coordinates. It does not generate bonds: the original pipeline infers them with
OpenBabel's perception from interatomic distances. That looked like a weak link, and a cheap measurement
appeared to confirm it — run on *true crystal geometry*, OpenBabel reproduces the deposited bond orders for only
54% of ligands, and 15% of its outputs will not sanitize at all.

So: give the diffusion model a bond channel, let it learn bonds jointly with atoms, and stop guessing from
distances. That was the plan, and the reasoning has a hole in it that took two days to find.

**54% faithful-to-crystal is not the pipeline's acceptance criterion.** `process_samples` does not ask whether a
bond order matches the PDB; it asks whether the molecule sanitizes, keeps its scaffold, and is not a duplicate.
The repair pass (`salvage_bonds`, `trim_stray_fragments`, `scaffold_orders`) absorbs most perception error before
that check happens. A metric can be genuinely alarming and still have almost no bearing on the number you care
about.

## What was built

A diffused bond channel on the frozen `crossdocked_fullatom_cond.ckpt` backbone:

- Bonds are one-hot over 5 classes (none, single, double, triple, aromatic) for every ligand atom pair, scaled by
  `1/4`, and noised with the same `alpha`/`sigma` schedule as the atom types.
- x0-parametrisation: the head emits class logits, the loss is cross-entropy over pairs with `t > 0`, and the
  sampler reconstructs `eps = (z - alpha * softmax / 4) / sigma`. Final bonds are the argmax at the last step.
- `BondNet` is a separate 3-layer message-passing pair classifier reading frozen backbone embeddings, noised
  atom types, 16-dim RBF distances, the noised bond state and `t`. Its last layer is zero-initialised, so the
  model starts exactly equal to the base checkpoint (verified: max output difference ~7e-6, initial bond
  CE = ln 5 = 1.6094).
- `BondDDPM.inpaint_bonds` does RePaint-style inpainting with a bond state. Scaffold bonds (both atoms fixed) are
  re-noised from the truth each step and recombined by mask, so they are held exactly.

It worked, as a model. After two epochs: `val_l2` 0.2109 against the base 0.218, so the atoms were never
damaged; `val_bond_ce` 0.1811; and on a fixed-true-atoms diagnostic, **connectivity precision 1.000, recall 1.000
(249/249 true bonds, 0 false), with 83% of bond types correct.**

## What it did to yield

The study is 15 cells x 2 batches x 16 samples = 480 molecules, scored by `score.py` through smeltery's own
`process_samples`, unrepaired (`FilterConfig()`) and repaired (`salvage_bonds=8, trim_stray_fragments=True,
scaffold_orders=True`). Every row below uses *identical atoms and coordinates* — only the bond source differs.

| Bond source | Accepted (unrepaired) | Unsanitizable | Accepted (repaired) | Unsanitizable (repaired) |
|---|---|---|---|---|
| Baseline, original run | 49 | 189 | 384 | 2 |
| OpenBabel, same atoms | 60 | 185 | 376 | 7 |
| Model bonds, argmax | 41 | 363 | 125 | 262 |
| Model bonds, valence-decoded | 36 | 204 | 187 | 180 |

A head at 97% precision/recall on crystal geometry produces 363 of 480 unsanitizable molecules on *generated*
geometry. The failure is distribution shift, not capacity: it learned to label bonds on real coordinates and was
asked to label bonds on sampled ones, where it assigns orders that imply impossible valences.

Valence-aware decoding — start from argmax, then repeatedly downgrade the least-confident non-scaffold bond
touching an atom RDKit flags — fixes the valences and breaks the molecules instead. Unsanitizable falls from 363
to 204, but 168 molecules come back disconnected, because the cheapest way to resolve an over-valent atom is
often to cut the bond holding the molecule together. Net repaired acceptance 187, against 376 for OpenBabel.

Fine-tuning the backbone on a larger kinase/PDE pool, tried in the same stretch, gave ~4% better denoising loss
and no yield change at all (`ft_B_last`: 44 unrepaired, 373 repaired).

## Two measurement errors, and the checks that caught them

Worth recording in detail, because they are the actual lesson.

**Conflating settings columns.** Comparing model bonds (`scaffold_lost: 1`) against OpenBabel (`scaffold_lost:
142`), I concluded that holding scaffold bonds fixed was a real advantage and that OpenBabel was mangling 142
scaffolds. The 142 is from the *unrepaired* column; the repaired figure is 19. At matched settings it is 18 versus
19 — no advantage in either direction. A rejection count is meaningless without the config that produced it.

**A diagnostic that measured its own bug.** To ask whether those scaffold losses were bond orders or real
connectivity change, I wrote a script that compared the repaired molecule's Kekulé-typed scaffold bonds against
the aromatic-perceived `ScaffoldSpec`. It reported 130 order-only failures and 0 connectivity failures, which I
read as "every scaffold loss is a representation artifact" — a tidy, wrong, and very appealing conclusion.
Checking those 130 against the real filter: **126 match `exact`, all 130 match `topology`, none were ever
rejected.** The 130 were an artifact of my comparison, not of the pipeline.

The check that settled it took ten minutes: run `process_samples` at the config in question and read its own
`Rejection` list. That should have been step one.

## Where the yield actually goes

OpenBabel path, repaired, 480 samples:

| Outcome | Count |
|---|---|
| Accepted | 376 |
| Duplicate | 70 |
| Scaffold lost | 19 |
| Forbidden substructure | 8 |
| Unsanitizable | 7 |

78% accepted, and the largest rejection category is the model resampling molecules it already produced — a
diversity problem, not a chemistry one. There was no bond-perception bottleneck left to recover. The 19 scaffold
losses are genuine chemistry changes (rearranged rings, altered cores), not representation mismatches.

## What actually helped

**The repair pass, by an enormous margin.** 49 accepted unrepaired against 384 repaired, on identical samples.
`salvage_bonds=8` carries most of it: unsanitizable drops from 189 to 2. Everything else in this project is noise
next to that factor of eight — and it was built before this work started.

**A fixed-timestep validation metric.** `FixedTValModel` evaluates l2 noise error on a seeded `T_GRID`
(`[25, 75, ... 475]`, `torch.manual_seed(1234 + batch_idx)`), which made `val_l2` stable enough to read. It
improved no output; it made the difference between a healthy run (0.21) and a collapsing one (1.0) visible
immediately, across roughly a dozen configurations. Without it none of the diagnosis below was possible.

## Training findings worth keeping

If anyone attaches a head to this backbone again:

- **Any bond-loss gradient reaching the backbone collapses the atoms.** Gradient scales 1.0, 0.1 and 0.02 all
  did it. The head needs a hard stop-gradient (`bond_grad_scale = 0.0`, via a `_GradScale` autograd function).
- **The encoder writing into atom embeddings needs its own, low learning rate.** Head and encoder at the same
  high multiplier collapses the atoms (`val_l2` 1.0). Three parameter groups: base at `lr`, encoder at
  `lr * enc_mult`, head at `lr * mult`.
- **Global-norm gradient clipping makes the atoms explode whenever BondNet is in the graph.** Both the model's
  own `clip_grad` and Lightning's `gradient_clip_val=5`. Bisection confirmed it: constant head output is fine,
  gradients are identical with and without the head (no leakage), `clip_grad=False` is fine, clipping explodes.
  The mechanism is still unexplained. Workaround: no clipping, plus a callback that zeroes non-finite steps.
- A small MLP pair head lacks the capacity: 10 of 249 bonds recovered. Joint message passing over pairs is needed.
- The sampler algebra was verified independently with an oracle-eps test (0 mismatches at T=50 and T=200), which
  is what localised an early failure to the head rather than the diffusion maths.

## The transferable lesson

Three independent signals pointed at bonds — a bad perception metric (54%), a large-looking rejection count
(142), and a high head accuracy (97%) — and not one of them was measuring acceptance. The generator's raw output
was improved repeatedly while the thing acceptance actually depended on was the post-processing, which was
already working.

Before optimising a stage, measure the stage's contribution to the number you care about, with the filter's own
output, at the settings you ship. Ten minutes of that beats two days of model work.

## Paths

| What | Where |
|---|---|
| Bond model, dynamics, DDPM, dataset, Lightning module | `external/sbdd-pool/bondmodel.py` |
| Training driver | `external/sbdd-pool/train_bond.py` |
| Bond-aware generation (writes model-bond and OpenBabel SDFs from the same atoms) | `external/sbdd-pool/bondgen.py` |
| Valence-aware decoder | `external/sbdd-pool/valence.py` |
| Fixed-timestep validation | `external/sbdd-pool/ftmodel.py`, `eval_val.py` |
| Crystal-geometry perception check (step 0) | `external/sbdd-pool/step0_crystal.py` |
| Fixed-atoms bond diagnostic; oracle-eps check | `external/sbdd-pool/diag_bonds.py`, `diag_oracle.py` |
| 15-cell study runner and scorer | `external/sbdd-study/run_bond_study.py`, `score.py` |
| Study outputs (`_ob` = OpenBabel on the same atoms, `_val` = valence-decoded) | `external/sbdd-study/bond_J*`, `bond_Jlast*` |
| Saved bond probabilities, for offline decoding experiments | `external/sbdd-study/bond_P/work/<cell>/probs_<b>.npz` |
| Archived failed training runs | `external/sbdd-pool/old_head/` |

## If you want to push yield anyway

The corrected numbers say the targets are duplicates (70 of 480 — sampling diversity, seeding across cells), and
then the 19 scaffold losses and 8 forbidden substructures, which are small and real. Not bonds. A decoder that
penalises disconnection as well as valence could close part of the model-bond gap, and `probs_<b>.npz` makes that
testable without regenerating, but the ceiling it is chasing is 78% acceptance that the existing path already
reaches.
