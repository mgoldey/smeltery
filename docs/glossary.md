# Glossary

Terms as smeltery uses them. Each entry names the page or source that owns the definition.

**Analogue.** A candidate other than the parent. Its pose *i* is built on the parent's pose *i*. See [paired_poses](tiers/paired_poses.md).

**Bench.** The web app for running, reviewing and commenting on runs. See [bench.md](bench.md).

**Bench gate.** The fast test set run before a commit: `tests/bench` plus `tests/test_study_plan_cells.py` (about 3 minutes). Defined in `AGENTS.md`, not a scientific gate.

**Campaign.** A named body of work on one target, such as the danuglipron halogen series. The decisions file records its name. See [campaign_danuglipron.md](campaign_danuglipron.md) and `src/smeltery/decisions.py`.

**Candidate.** A molecule moving through the funnel, carrying its pose ensemble and its per-pose results (`smeltery.model.Candidate`). See [gui-contract.md](gui-contract.md).

**Charge sensitivity.** How far swapping one point-charge model for another moves the scores (`gates.charge_sensitivity`). The result raises the cut's floor to that value; opt-in via `[charge_sensitivity]`. See [tiers/index.md](tiers/index.md) (gate G9).

**Common core.** The maximum common substructure (MCS) shared by parent and analogue. Its atoms are copied exactly from the parent pose; a pair with no usable core is refused (gate G2, `NoCommonCoreError`). See [paired_poses](tiers/paired_poses.md).

**Cut.** The funnel's decision step (`funnel.cut`): sort candidates by mean ddE, group neighbours that are not resolved, and keep `keep` of them, or refuse. Lower is better. See [worked-example.md](worked-example.md).

**dE_int.** The field interaction energy of one pose: E(in the pocket field) minus E(vacuum) at the same geometry, in kcal/mol. A difference, so it compares across formulas. It is a fixed-geometry proxy and not a binding free energy. See [field_interaction](tiers/field_interaction.md).

**ddE (paired ddE, ΔΔE).** The unit smeltery ranks on: analogue pose *i* minus parent pose *i* of a per-pose quantity, then averaged (`funnel.paired_delta`). Never a total energy, because totals scale with electron count. See [methods.md](methods.md) and [gui-contract.md](gui-contract.md).

**Decision.** A keep, reject or hold made by a person on a candidate, with a reason. Stored in a decisions file separate from the run record, and readable by the next run via `[decisions] file`. See [golden-path.md](golden-path.md) and `src/smeltery/decisions.py`.

**Digest (input digest).** The hash of a run's inputs and tiers; names the run record `run-<digest12>.json`. Results added to the record do not feed it. See [gui-contract.md](gui-contract.md).

**Field model.** The point-charge model used for the pocket, named by the pdb2pqr force field of the primary pocket. Recorded per SCF in `calcs.jsonl`. See [bench.md](bench.md).

**Floor (systematic floor).** The size below which a difference is not meaningful because of an error the SEM cannot see, such as the charge model. A difference must exceed it to be resolved. `None` means unmeasured. See [tiers/index.md](tiers/index.md) and `src/smeltery/floor.py`.

**Floor, absolute and difference.** An absolute floor bounds the error in one value; a difference floor bounds the error in a difference under one condition, where shared shifts cancel. They are not interchangeable (`smeltery.floor.Floor`).

**Funnel.** The staged pipeline that scores a series of analogues and cuts it down. It ranks only what it can resolve. See [README.md](../README.md).

**Gate.** A refusal built into the pipeline: it raises or stops a run instead of producing a number that cannot be trusted. G1 to G9 are listed in [tiers/index.md](tiers/index.md). Gate stages in a run (parity, PoseBusters, MMFF strain) can only reject.

**Golden path.** The documented route from a target to a shareable bundle: check, run, inspect, decide, bundle. See [golden-path.md](golden-path.md).

**G1 to G9.** The ids of the gates: pose validity, common core, electron parity, unmeasured floor, not a free energy, same formula, declared quantity, declared unit, charge-model sensitivity. See [tiers/index.md](tiers/index.md).

**INCOMPLETE.** A bench verdict state: a required result key is missing, for example when a task died mid-SCF. Never shown as blank or as a winner. See [bench.md](bench.md).

**is_delta_g.** A tier attribute; when False, the tier's output is a rank-only score and its differences are refused (gate G5). See [tiers/index.md](tiers/index.md).

**Keep.** The `[cut] keep` setting: how many candidates survive a stage. Also a decision status (`keep`, `reject`, `hold`).

**MCS.** Maximum common substructure, the search that finds the common core. It has a timeout (`mcs_timeout_s`). See [paired_poses](tiers/paired_poses.md).

**Method.** One entry of the catalog that a stage runs, declared as a `MethodSpec`. See [methods.md](methods.md) and [gui.md](gui.md).

**Pair.** A parent pose and the analogue pose with the same index. Pairing is by index, set up by the pose tier.

**Parent.** The reference molecule every analogue is compared against. The parent is also a candidate and its poses define the pairing.

**Pocket field.** The point charges cut from the prepared receptor around the site and used as the electrostatic environment for SCFs. See [bench.md](bench.md) and [field_interaction](tiers/field_interaction.md).

**Pose.** One geometry of a molecule in the pocket, from conformer generation or docking. Per-pose values are aligned with `Candidate.poses`. See [paired_poses](tiers/paired_poses.md).

**PoseBusters.** A pose-validity check used by gate G1; a tier run on poses that failed it raises `PoseGateError`. See [tiers/index.md](tiers/index.md).

**Provider.** A pluggable implementation of structure, docking, scoring, property or potential steps. See [providers.md](providers.md).

**Provisional.** The status of the measured limits: each rests on one system or one model pair. See [limits.md](limits.md).

**Quantity.** A named per-pose value a tier produces, such as `dE_int`, with a declared unit. Differences are only taken within one quantity. See [tiers/index.md](tiers/index.md) (G7, G8).

**Remedy ladder.** The sequence of fallback settings tried for an SCF that does not converge. See [bench.md](bench.md) and [rung](#rung).

**Resolved.** Two measurements are resolved when |Δmean| exceeds both z times the root sum of squares of their SEMs and the floor (`funnel.resolved`). A measurement with no SEM never resolves. Also a bench verdict state: a cut with survivors. See [bench.md](bench.md).

**Rung.** Two meanings. In the GUI and method catalog, a level of the method hierarchy: input, geometry, check, empirical, semiempirical, ab_initio, embedded_qm, designable, decision ([gui.md](gui.md)). In `calcs.jsonl`, the remedy-ladder step that produced a value, 0 without a ladder ([bench.md](bench.md)).

**SEM.** Standard error of the mean, s / sqrt(n) with the ddof=1 standard deviation. n < 2 gives NaN. See `Measurement.from_samples` in `src/smeltery/model.py`.

**Paired SEM.** The SEM of the per-pose paired differences. Shared pose effects cancel, so it is smaller than the unpaired SEM when parent and analogue are positively correlated. See `funnel.unpaired_delta` and `tests/test_sem_bootstrap.py`.

**Stage.** One step of a run's pipeline, running one method. See [gui.md](gui.md).

**Survivor.** A candidate kept by a cut. Survivors are set only when the `keep` boundary falls cleanly between two tie groups. See `CutResult` in `src/smeltery/funnel.py`.

**Temporal run.** A long pipeline run as a durable Temporal workflow (`smeltery run --temporal`), with the same science as an ordinary run. See [temporal-runs.md](temporal-runs.md).

**Tie group.** A run of candidates whose neighbours are not resolved from each other. Within a group there is no order. See [gui.md](gui.md) and `CutResult`.

**Tier.** A registered scoring class with a page under `docs/tiers/` covering what it computes, its cost, floor and what it may claim. See [tiers/index.md](tiers/index.md).

**UNRANKED.** The cut could not order the candidates because a tie group spans the `keep` boundary. Nothing is dropped, and the run still exits 0. See [bench.md](bench.md) and [worked-example.md](worked-example.md).

**z.** The multiplier on the combined SEM in `resolved`; default 2.0 (`funnel.cut`).
