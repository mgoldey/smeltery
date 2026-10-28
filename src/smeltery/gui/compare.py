"""Comparing several runs: per-key paired differences, and whether the floor can resolve them.

The rest of `smeltery.gui` reads ONE run: one parent, its analogues, one cut. A real study is
usually several runs that share a question -- two generators over the same cells, one setting swept,
the same series on two targets -- and pooling their candidates is the mistake this module exists to
prevent.

**Why pooling is wrong.** Each run has its own parent and its own mixture of candidates. A median
over all of them compares mixtures, not arms. Measured on the gen-compare scaffold-growth study: the
pooled medians of two generators differed by -3.24 kcal/mol, and the same comparison done per cell
gave **-1.21** -- two thirds of the apparent difference was which cells each arm happened to accept
molecules in, because the cells differ by ~9 kcal/mol among themselves. The pooled number is
reported here only beside the per-key one, labelled as confounded, so the gap is visible rather than
discovered later.

**What a difference is allowed to mean.** A per-key difference is a difference of summary statistics
over DIFFERENT molecules, not a paired measurement of one molecule in two conditions, so it carries
no SEM from `Measurement` and this module computes none. What it does carry is the floor: a
difference smaller than the systematic floor under both arms is **not resolved**, and `resolve()`
says so per key and in aggregate. That is the same rule `funnel.resolved` applies within a run,
applied to the only thing comparable across runs.

Nothing here ranks arms. It reports per-key differences, how many keys each arm leads, and how many
the floor can resolve; "unresolved" is an answer.
"""

from __future__ import annotations

import statistics as st
from dataclasses import dataclass, field

from .results import Run, ddE_table


#: What a key's two arms produced, and the difference, for one comparison key (a cell, a target, a setting).
@dataclass(frozen=True)
class KeyRow:
    """One comparison key. `a`/`b` are the arms' medians; `diff` is a - b, or None when an arm is empty.

    `n_a`/`n_b` count the molecules each arm contributed. A key where either arm is empty is UNPAIRED:
    it is reported, never dropped, because an arm producing nothing for a key is a result about that arm.
    """

    key: str
    n_a: int
    n_b: int
    a: float | None
    b: float | None
    diff: float | None
    resolved: bool | None = None

    @property
    def unpaired(self) -> bool:
        return self.diff is None

    @property
    def leader(self) -> str | None:
        """Which arm is more favourable (more negative), or None when unpaired or exactly equal."""
        if self.diff is None or self.diff == 0.0:
            return None
        return "a" if self.diff < 0 else "b"


@dataclass
class Comparison:
    """Two named arms, each a mapping of comparison key -> the values that arm produced for it.

    Built from runs with `from_runs`, or directly from values with `from_values` when the numbers come
    from outside a run record (a study script, a generator sweep). `floor` is the systematic floor under
    BOTH arms, in the same unit; a difference below it is not resolved. `floor=None` means no floor is
    known, and then `resolved` is None everywhere rather than True -- an unmeasured floor is not a
    licence to call differences real.
    """

    label_a: str
    label_b: str
    values_a: dict[str, list[float]]
    values_b: dict[str, list[float]]
    floor: float | None = None
    unit: str = "kcal/mol"
    quantity: str = "dE_int"
    notes: list[str] = field(default_factory=list)

    @classmethod
    def from_runs(
        cls,
        arm_a: dict[str, Run],
        arm_b: dict[str, Run],
        *,
        label_a: str,
        label_b: str,
        floor: float | None = None,
        quantity: str = "dE_int",
    ) -> Comparison:
        """Each arm is {key: Run}. Takes every analogue's paired mean from `ddE_table`, so the per-run
        pairing against that run's own parent is kept and only the summaries are compared."""

        def harvest(arm: dict[str, Run]) -> dict[str, list[float]]:
            return {k: [r["mean"] for r in ddE_table(run)] for k, run in arm.items()}

        return cls(label_a, label_b, harvest(arm_a), harvest(arm_b), floor=floor, quantity=quantity)

    @classmethod
    def from_values(
        cls,
        values_a: dict[str, list[float]],
        values_b: dict[str, list[float]],
        *,
        label_a: str,
        label_b: str,
        floor: float | None = None,
        unit: str = "kcal/mol",
        quantity: str = "dE_int",
    ) -> Comparison:
        return cls(label_a, label_b, dict(values_a), dict(values_b), floor=floor, unit=unit, quantity=quantity)

    @property
    def keys(self) -> list[str]:
        """Every key either arm produced anything for, sorted, so an arm's gaps stay visible."""
        return sorted(set(self.values_a) | set(self.values_b))

    def rows(self) -> list[KeyRow]:
        """One `KeyRow` per key, in key order. Unpaired keys keep their place."""
        out = []
        for k in self.keys:
            va, vb = self.values_a.get(k, []), self.values_b.get(k, [])
            ma = st.median(va) if va else None
            mb = st.median(vb) if vb else None
            diff = (ma - mb) if (ma is not None and mb is not None) else None
            res = None if (diff is None or self.floor is None) else abs(diff) > self.floor
            out.append(KeyRow(k, len(va), len(vb), ma, mb, diff, res))
        return out

    def summary(self) -> dict:
        """The aggregate, and the pooled value beside it so the confound is visible, never alone."""
        rows = self.rows()
        paired = [r for r in rows if not r.unpaired]
        diffs = [r.diff for r in paired]
        pooled_a = [v for vs in self.values_a.values() for v in vs]
        pooled_b = [v for vs in self.values_b.values() for v in vs]
        pooled_diff = st.median(pooled_a) - st.median(pooled_b) if pooled_a and pooled_b else None
        median_diff = st.median(diffs) if diffs else None
        resolved = [r for r in paired if r.resolved]
        return {
            "label_a": self.label_a,
            "label_b": self.label_b,
            "quantity": self.quantity,
            "unit": self.unit,
            "n_keys": len(rows),
            "n_paired": len(paired),
            "n_unpaired": len(rows) - len(paired),
            "median_per_key_diff": median_diff,
            "diff_range": (min(diffs), max(diffs)) if diffs else None,
            "keys_favouring_a": sum(1 for r in paired if r.leader == "a"),
            "keys_favouring_b": sum(1 for r in paired if r.leader == "b"),
            "floor": self.floor,
            "n_resolved": len(resolved) if self.floor is not None else None,
            "aggregate_resolved": (
                None if (self.floor is None or median_diff is None) else abs(median_diff) > self.floor
            ),
            "pooled_a_median": st.median(pooled_a) if pooled_a else None,
            "pooled_b_median": st.median(pooled_b) if pooled_b else None,
            "pooled_diff_CONFOUNDED": pooled_diff,
            "n_molecules_a": len(pooled_a),
            "n_molecules_b": len(pooled_b),
        }

    def verdict(self) -> str:
        """One sentence a reader can quote, which says 'not resolved' whenever that is the honest answer."""
        s = self.summary()
        if not s["n_paired"]:
            return f"No key has both arms, so {self.label_a} and {self.label_b} cannot be compared."
        md, unit = s["median_per_key_diff"], self.unit
        lead = self.label_a if md < 0 else self.label_b
        counts = f"{s['keys_favouring_a']} of {s['n_paired']} keys favour {self.label_a}"
        if self.floor is None:
            return (
                f"Median per-key difference {md:+.2f} {unit} ({counts}); no floor is measured, "
                f"so whether that is resolvable is unknown."
            )
        if abs(md) > self.floor:
            return (
                f"Median per-key difference {md:+.2f} {unit} favours {lead}, above the {self.floor:.2f} "
                f"{unit} floor ({counts}; {s['n_resolved']} of {s['n_paired']} keys individually resolved)."
            )
        return (
            f"NOT RESOLVED: median per-key difference {md:+.2f} {unit} is within the {self.floor:.2f} "
            f"{unit} systematic floor ({counts}), so this comparison does not separate the arms."
        )
