# Baseline control: report whether ddE beats a cheap baseline on this campaign

Date: 2026-11-28. Status: built.

## Problem

L15 (tyk2: single-snapshot MM/GBSA rho +0.888 against ddE +0.252) and L5 (cdk2: Crippen logP +0.79 against ddE +0.69)
say a baseline that costs seconds can rank a series as well as ddE or better. A user running a campaign with
experimental labels has no way to ask the same question of their own series, and a run record says nothing about it.

## Decisions

- **An `analysis` method, `[baseline]`, opt-in.** The analysis slot already runs after the cut, merges one dict into
  `results[key]` and is skipped for a partial run. Absent section: no hook runs, the digest gets no `baseline` input,
  the record has no `baseline` key; a test compares the bytes with the method unregistered. On: the only new key is
  `results.baseline`; a test checks every other key and the cut are unchanged. A new tier was not used: a tier
  measures a quantity the cut ranks on, and this must never rank.
- **Statistic: Delta-rho = rho(baseline) - rho(ddE) vs the labels, paired bootstrap 95% CI**, by the code L15 used
  (`plb_measure.paired_delta_spearman_ci`: one ligand resample for both correlations). Positive means the baseline
  ranks the series better, as in `docs/benchmarks/plb_mmgbsa.md`. logP is the raw Crippen logP of each analogue, as in
  L5.
- **Sample size: the L4/L8 logic** (`plb_measure.power_simulation`): smallest significant two-sided |rho| of the exact
  permutation null and power at a true Pearson r = 0.7. Fewer than 5 labelled analogues: no |rho| is significant, so
  the verdict is `n too small to conclude` and no statistic is reported. Below 80% power the verdict carries
  `underpowered` with the numbers. Above n = 100 the normal approximation replaces the simulation (power omitted).
- **Verdict phrases are a closed set** (`baseline.PHRASES`). The CI decides, never the point estimate. A CI containing
  0 is `indistinguishable ... not evidence they are equivalent`; never "matches" or "equal". "ddE ranks better" is
  allowed only with the CI excluding 0 and says it is one series. The CI-excludes-0 rule for "does not beat" is the one in
  the labels of `docs/benchmarks/plb_mmgbsa.md`.
- **MM/GBSA is not computed by smeltery.** The only implementation is `scripts/plb_mmgbsa.py` (PLB-shaped inputs; not
  importable). So: `mmgbsa_file` (ddG_MM per analogue computed elsewhere) is used as given; `mmgbsa = true` without a
  file needs openmm, openmmforcefields, openff-toolkit and `sqm` already installed (checked with `find_spec` and
  `which`, nothing imported or installed) and is otherwise recorded as skipped with the missing ones named. Packages
  present and no file: skipped, saying there is no in-process engine (`methods.baseline.MMGBSA_ENGINE` is the
  injection point). The record never shows a made-up MM/GBSA number.
- **Without labels** the baselines are still computed and recorded and nothing is compared.

## Not done

- No in-process MM/GBSA engine; no multiple-series pooling (L15's joint rule needs two series).
- Not run per-activity under Temporal as its own activity: it is an ordinary analysis and runs in the record stage.
- Labels are read from a file only; no GUI form beyond the registry-derived knob list.

## Tests

`tests/test_baseline_control.py`: synthetic ddE and labels, no SCF, no quantum chemistry. Pins L4's 0.738 and L8's
0.587, each verdict phrase, seeded bootstrap, skip reasons, off-by-default byte identity, the cut unchanged.
