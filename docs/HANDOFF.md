# smeltery handoff: M1/M2 status and next steps

As of 2026-10-07. `main` is at `fbf7c36` (merge of PR #47).

## Where things stand

Most of M1 and M2 is merged to `main`. Four things are not finished:

1. **#6, the halogen-scan reproduction.** The code and an n=1 result exist, nothing is pushed, and the premise of the issue turned out to be wrong (see Findings).
2. **#11, the `active_site` migration.** Draft PR #49. CI is green, but its headline number does not reproduce (-28.758 kcal/mol, not -17.41).
3. **#13, removing `tools/` from ferric.** Not started. It edits the ferric repo, so it needs the owner's go-ahead.
4. **#14, build identity.** The smeltery half is merged (PR #44). The ferric half is filed as mgoldey/ferric#331 and not implemented.

Nothing is running. Do not relaunch heavy compute until the owner says so.

## Merged to main

| PR | Issue | What |
|---|---|---|
| #36 | #1 | CI test workflow, with a `MIN_TESTS` collected-count gate |
| #37 | #2 | Tier protocol v2: `produces()`, `systematic_floor()`, unit-aware `cut` |
| #38, #39, #40, #41 | #3, #5, #4, #7 | Pocket field loader; xtb GFN2 tier; charge-sensitivity gate; cost model. Landed through the integration PR #42 |
| #43 | #8 | `docs/migration.md` and the boundary tests (`tests/test_migration_rules.py`) |
| #44 | #14 (smeltery half) | `record.py` prefers `ferric.__build__` when ferric provides it |
| #45 | #9 | `smeltery.structure` readers; charge and multiplicity are always explicit |
| #46 | #12 | `smeltery.generate`: morph, isomers, substitution as candidate generators |
| #47 | #10 | `smeltery.docking`: `DockingProvider`, Vina/Meeko behind a `docking` extra |

Check that the issues above actually closed. #14 should stay open on purpose.

## Open work

| Item | State | Next step |
|---|---|---|
| PR #49 (issue #11) | Draft. CI green after a lazy-ferric-import fix. `compute_binding_energy` on 7LCJ gives -28.758 kcal/mol at def2-svp; the issue says -17.41. | Run the binding energy at STO-3G to test the hypothesis below. Then fix the issue text or the test, and mark ready. Do not close #11 from the PR until then. |
| Issue #6 | Branch `m1-06-halogen-reproduction` has 3 local commits (`acd5abb`). The `--sites` and `--skip-model-b` edits are uncommitted. Not pushed. Issue body already amended. | Decide how far to take the run (see Decisions). Rebase onto `main`, fix `MIN_TESTS`, open a draft PR. |
| Issue #13 | Not started. Needs #9 to #12 (done, except #11 is a draft). | Owner's go-ahead. First decide `mm_topology` and `solvate` (see below). |
| ferric#331 | Filed. Not implemented. | Implement `ferric.__build__ = {"git_sha", "dirty"}` in ferric. smeltery's `_ferric_build()` in `record.py` is the only consumer. |

For #13: `mm_topology.py` is dropped from smeltery with no re-export, so ferric must keep it (and its test) before `tools/` is deleted. `solvate.py` is in `active_site` but not in #11's list and needs a move-or-drop decision. campaign, tox, viz and the rest of pipeline have no migration issue, although #13 deletes all of `tools/`. `docs/migration.md` marks their placement as inferred.

## Findings that change the plan

**1. The campaign's halogen scan was not a ferric QM calculation.** It scored ligands with a classical Coulomb sum of atom-centred point charges against the 6458 pocket charges. Charges were hand-assigned per element, with Gasteiger as the second model. Its own verdict was that this probe cannot rank substituents, and that QM/MM is the right instrument. smeltery's `FieldInteraction` runs ferric RHF in the pocket field, so the two are different models. Criterion (2) of #6 ("agrees with the campaign's recorded value within SEM") could not be met, and I rewrote it as "tabulate paired ddE with SEM, compare to the campaign only as ranges". The per-analogue table is not recoverable; only CH3 at site 9 survives (+0.879 per-element, +22.526 Gasteiger). Sources are ferric project memories `halogen-scan-ranking-is-the-charge-model` and `pocket-pairing-rho-is-0999-but-anchor-is-not-zero`, under `~/.claude/projects/-home-matt-qc-ferric/memory/`.

**2. The campaign's self-anchor (+0.31) came from re-embedding the analogue.** smeltery copies mapped atoms exactly, so its self-anchor is 0.0 by construction. The 12 analogues in the script (F, Cl, CH3 at four ring sites) are my agent's own choice, not known to be the campaign's.

**3. The -17.41 in #11 is probably a STO-3G number.** `FieldInteraction` defaults to STO-3G. Its parent dE_int at pose 0 in the AMBER pocket field is -17.41 kcal/mol, which matches the number #11 could not reproduce at def2-svp. This is a hypothesis: I have not run #11's own `compute_binding_energy` at STO-3G.

**4. The halogen result so far is n=1, bzim-C9 site only, AMBER charges, STO-3G, neutral acid.**

| Quantity | Value (kcal/mol) |
|---|---|
| Parent binding energy, pose 0 | -17.41 |
| Self-anchor, poses 0 and 1 | exactly 0.0 |
| Paired ddE, F | -4.15 |
| Paired ddE, Cl | -8.40 |
| Paired ddE, CH3 | +18.68 |

With one pose there is no SEM, so criteria 3 (paired SEM below unpaired) and 4 (sensitivity floor and cut) are untested; criterion 3 prints "FAIL" only because the SEM is undefined. The run record is `out/danuglipron-halogen-95060418a49b.json` in the #6 worktree.

**5. The 2-pose run died on an unconverged SCF.** `RuntimeError: bzim-C9-Cl pose 0: SCF did not converge` (the "pose 0" is the worker-local index; the failing job was Cl at pose 1). The script has no handling for this, so one failure aborted the run and discarded the F and CH3 jobs still in flight. Two jobs (the parent and the self-pair at pose 1) finished and are cached.

**6. Cost.** A pose took about 57 minutes on 2 niced cores under heavy load, and a finished job used about 28 CPU-minutes when each worker had its own core. #7's cost model underestimates this system, probably because it never saw a 6458-charge field (unverified).

## Decisions waiting on the owner

1. **#6:** stop at the n=1 result and write it up, or make non-converged SCFs a recorded failure and rerun F and CH3 at pose 1 (about 1 to 2 hours on 6 workers) to get a weak n=2 SEM. The CHARMM pass (needed for criterion 4) is a separate overnight job.
2. **#11:** confirm the STO-3G hypothesis (one heavy run) before touching the issue text.
3. **#13:** go-ahead to edit the ferric repo, and the `solvate` and campaign/tox/viz/pipeline placement decisions.
4. **ferric#331:** who implements it.

## How to resume

**CPU rules from the owner.** At most 2 cores unless told otherwise, and one worker computing at a time. The owner later allowed 6 workers for the reduced halogen run, then asked to pause before any relaunch. Prefix heavy commands with `nice -n 19 taskset -c 0,1` and set `OPENBLAS_NUM_THREADS=1 RAYON_NUM_THREADS=2 OMP_NUM_THREADS=1`.

**Rerun the halogen script (resumes from its cache):**

```
cd .claude/worktrees/agent-ab3a4744d2827b646
uv run python examples/danuglipron_halogen.py --poses 1 --workers 1 --sites bzim-C9 --skip-model-b --out out
```

The cache is `out/halogen-scf-cache-1poses.json` (7 entries merged from the earlier runs). `out/` is gitignored, so copy the run record somewhere tracked if it should be kept.

**Worktrees.** Agent worktrees live under `.claude/worktrees/agent-*` (gitignored). The one for #6 is `agent-ab3a4744d2827b646`, for #11 is `agent-a9f7db6f65ac7bd10`. Many merged branches and worktrees can be removed.

**Gotchas learned**

- Every M2 PR conflicts on the `MIN_TESTS` line in `.github/workflows/test.yml`. Merge `main` in, take the real count from `uv run pytest --collect-only -q`, and commit.
- CI does not run on a retarget. Retarget a stacked PR to `main` first, then push (an empty commit works), or no check appears.
- `pkill -f <pattern>` matches the shell running it and kills it (exit 144). Kill by PID.
- Do not run git inside the ferric checkouts; read files only. The pinned ferric commit is `8637a5d`, and the local `/home/matt/qc/ferric` has diverged from it in places (for example `structure`, `united_atom`).
- A hosted draft of this handoff also exists at https://claude.ai/code/artifact/0eaeae22-6695-4774-9b35-943f4800fa20 with only its first section filled. This file supersedes it; delete it if unwanted.
