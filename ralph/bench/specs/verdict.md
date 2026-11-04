# Verdict states

**Every run record maps to exactly one of RESOLVED, UNRANKED, INCOMPLETE.**

## Requirements

1. `smeltery.bench.verdict.verdict_of(record: dict) -> Verdict`, where `Verdict` is frozen with
   `state`, `winner: str | None`, `floor: float | None`, `missing: tuple[str, ...]`.
2. INCOMPLETE when any of `results.paired_ddE`, `results.unpaired_sem`, `results.cut`,
   `results.funnel` is absent; `missing` names them. INCOMPLETE takes precedence over everything.
3. UNRANKED when the cut is unranked: `winner` is None and `floor` is set.
4. RESOLVED otherwise; `winner` is the first survivor.
5. INCOMPLETE and UNRANKED never carry a winner.
6. `verdict_of` reads `record["results"]` directly. It must not call `gui.results.load_run`, whose
   refusal of partial records exists on purpose and is not relaxed.
7. Every surface that shows a run shows its verdict. INCOMPLETE is rendered as its own state with
   what is missing, never as a blank: a blank turns "no data" into "no difference".

## Data source for checks
Records written by `tests/gui_fixture.make_run_dir` (RESOLVED), `make_run_dir(unranked=True)`
(UNRANKED), and the same record with `results.cut` deleted (INCOMPLETE).
