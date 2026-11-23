"""Two arms compared key by key: each key's paired ddE in arm A against arm B (`smeltery.gui.compare.Comparison`)."""

from __future__ import annotations

from dataclasses import dataclass

from smeltery.gui.compare import Comparison, candidate_rows


def arm_labels(ra, rb) -> tuple[str, str]:
    """Each arm's campaign. When both arms share one (two runs of one campaign, the usual comparison), the arm letter
    the page chose it as and its digest are added, so the caption never says a run favours itself."""
    if ra.campaign != rb.campaign:
        return ra.campaign, rb.campaign
    return (
        f"A ({ra.campaign} {ra.record['input_digest'][:8]})",
        f"B ({rb.campaign} {rb.record['input_digest'][:8]})",
    )


@dataclass(frozen=True)
class RunComparison:
    comparison: Comparison
    #: per-analogue paired ddE of the two runs; None (with `candidates_note` saying why) when their parents differ
    candidates: list[dict] | None
    candidates_note: str | None


class CompareArms:
    def __init__(self, run_store):
        self.run_store = run_store

    def run(self, arm_a, arm_b, *, label_a="a", label_b="b", floor=None, quantity="dE_int") -> Comparison:
        """Arms of several runs: each {key: run, or the path of one}."""

        def resolve_arm(arm):
            out = {}
            for k, v in arm.items() if isinstance(arm, dict) else enumerate(arm):
                if isinstance(v, str):
                    v = self.run_store.read_run(v)
                out[str(k)] = v
            return out

        return Comparison.from_runs(
            resolve_arm(arm_a), resolve_arm(arm_b), label_a=label_a, label_b=label_b, floor=floor, quantity=quantity
        )

    def runs(self, ra, rb) -> RunComparison:
        """Two runs as arms, one key each, labelled by `arm_labels`. A difference is resolved only past the larger
        of the two runs' floors, and the per-candidate rows use the larger of their z."""
        label_a, label_b = arm_labels(ra, rb)
        cmp = self.run({"run": ra}, {"run": rb}, label_a=label_a, label_b=label_b, floor=max(ra.floor, rb.floor))
        candidates, note = candidate_rows(ra, rb, max(ra.z, rb.z))
        return RunComparison(cmp, candidates, note)
