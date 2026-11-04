from __future__ import annotations

from smeltery.gui.compare import Comparison


def _as_arm(x):
    if isinstance(x, dict):
        return x
    if isinstance(x, (list, tuple)):
        return {str(i): (r if not isinstance(r, str) else None) for i, r in enumerate(x)}
    return x


class CompareArms:
    def __init__(self, run_store):
        self.run_store = run_store

    def run(self, arm_a, arm_b, *, label_a="a", label_b="b", floor=None, quantity="dE_int"):
        # resolve paths to Run objects if strings
        def resolve_arm(arm):
            out = {}
            for k, v in arm.items() if isinstance(arm, dict) else enumerate(arm):
                if isinstance(v, str):
                    v = self.run_store.read_run(v)
                out[str(k)] = v
            return out

        a = resolve_arm(arm_a)
        b = resolve_arm(arm_b)
        return Comparison.from_runs(a, b, label_a=label_a, label_b=label_b, floor=floor, quantity=quantity)

    def execute(self, *a, **kw):
        return self.run(*a, **kw)

    __call__ = run
