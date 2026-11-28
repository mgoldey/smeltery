# Generator rejections in the run record

Date: 2026-11-28. Status: built. Closes the "Attrition" gap in [gui-review.md](../../gui-review.md) and the first plan item's
last bullet ("generator rejections, when a generator ran").

## Problem

Gates stop a run rather than drop candidates, so `results.funnel` is nearly flat. The attrition that exists happens
earlier, in a generator: `GenerationResult.rejected` holds every raw output it declined, each with a reason from the
closed `generate.base.REASONS`. A run record never held it, so a reader could not tell 4 candidates from 4 survivors of 400.

## Decisions

1. **A generation file, named by the config, like `[decisions]`.** `smeltery.generate.record.write_generation(path,
   result)` writes `generation.json` (`smeltery.generation/1`: generator name, `n_returned`, and every rejection:
   index, reason, detail, SMILES). A config adds `[generation] file = "generation.json"`. The run has no other way to
   learn what a generator did: generation is a library call that happens before the config exists.
2. **Where it lands.**
   - `results.funnel` gets a leading row `{stage: "generate", kind: "generate", candidates_in: raw outputs,
     candidates_out: returned, generator, rejected_by_reason, rejected_total, rejected_listed, rejected: [...]}`.
     Every existing reader of funnel rows already takes `candidates_in/out`, so the Marimo funnel and the HTML report
     show the generator as the first bar with no change.
   - `results.generation_in = {file, sha256}`, beside `decisions_in`; the file becomes a core-record `data` input.
   - `inputs.generation = {sha256}` only when the section is present: the file is an input, so it moves the digest.
3. **Closed reasons, complete counts.** `rejected_by_reason` lists every reason in `REASONS` order, zero included, so two
   runs compare cell by cell. The SMILES list is capped at `REJECTED_SMILES_CAP = 50` in raw-output order;
   `rejected_listed` of `rejected_total` says when it was cut. The file keeps all of them.
4. **The frozen core schema is untouched.** `attestation.run/1` is frozen, so the block goes under
   `ext.smeltery.generation` (the funnel row minus `stage` and `kind`). The funnel row also yields the usual
   `stage.candidates_in|survivors` metrics with `stage: generate`; no new metric names.
5. **Dataset export.** The Parquet table's columns are unchanged. A run with a generation step adds
   `smeltery.generation` to the file metadata (run_id -> generator, raw, returned, counts per reason; no SMILES). A dataset
   with no such run has byte-identical metadata.
6. **Bench.** `GET /api/runs/{id}/attrition` returns the funnel stages and, when present, the generation block
   (`n_raw`, `n_returned`, counts per reason, the capped list). The run's Results page shows "Where candidates were lost"
   above the ΔΔE table only for a run with a generation step.
7. **Records with no generation step are byte-identical.** Every addition is conditional on `[generation]`; the committed
   core-record golden and the prebuilt runs are the check.

## Not done

- The CLI does not run a generator. Producing `generation.json` is the caller's step (`write_generation`).
- Candidates in `[candidates]` are not cross-checked against the generator's output: the config stays the source of
  what is measured, and the record states only what the generator reported.
