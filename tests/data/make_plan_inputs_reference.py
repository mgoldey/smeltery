"""Regenerate tests/data/plan_inputs_reference.json: the `--plan` inputs and tiers of every example and prebuilt
config, as `build_plan` computes them.

This is the reference the digest tests compare against. It exists because an input digest is a SHA-256 over
full-precision floats, and a few of those floats are geometry: a placed `[pocket.site]` charge sits a fixed
distance along a bond vector of the parent's RDKit conformer, and that conformer is ETKDG (seeded, so stable)
followed by an MMFF cleanup, which is an iterative floating-point minimisation. A hash of such a value can only
say "not equal"; these values can say which coordinate moved and by how much, and can hold a tolerance below
anything chemically meaningful. The exact digest is still asserted alongside, by the companion test.

Every value is written with `repr`, so floats round-trip exactly and a reader can see the full precision the
digest is taken over.

Regenerate only on a machine that still reproduces the pinned digests (the test asserts this, so a bad reference
cannot pass unnoticed), with the docking extra and pdb2pqr30 installed, or the configs needing them are left out
and keep whatever the file already holds.

Usage, from the repository root: .venv/bin/python tests/data/make_plan_inputs_reference.py
Not collected by pytest.
"""

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "tests" / "data" / "plan_inputs_reference.json"

sys.path.insert(0, str(ROOT / "tests"))


def main() -> int:
    from test_methods_registry import DIGESTS_AT_7389ABA, ONLY_CANDIDATE

    from smeltery.cli import build_plan, load_config
    from smeltery.pocket import pdb2pqr_version
    from smeltery.record import RunRecord

    have_docking = all(importlib.util.find_spec(m) is not None for m in ("vina", "meeko"))
    have_pdb2pqr = pdb2pqr_version() is not None  # the same check the suite and the loader use
    old = json.loads(OUT.read_text()) if OUT.is_file() else {}
    out = dict(old)

    for config in sorted(DIGESTS_AT_7389ABA):
        text = (ROOT / config).read_text()
        needs_docking = "[docking]" in text or "[structure]" in text
        needs_pqr = "[structure]" in text or ('file = "' in text and ".pdb" in text)
        if (needs_docking and not have_docking) or (needs_pqr and not have_pdb2pqr):
            print(f"  kept  {config} (its extra is not installed here)")
            continue
        cfg = load_config(ROOT / config)
        only = ONLY_CANDIDATE.get(config)
        if only is not None:
            cfg = {**cfg, "candidates": {k: v for k, v in cfg["candidates"].items() if k in (cfg["parent"], only)}}
        # A scratch out_dir, as the test passes tmp_path: a `[structure]` config writes its prepared receptor
        # under one. It is thrown away; only the plan's inputs and tiers are the reference.
        with tempfile.TemporaryDirectory() as tmp:
            plan = build_plan(cfg, out_dir=Path(tmp))
        digest = RunRecord(plan.cfg["campaign"], plan.inputs, plan.tiers, {}).input_digest
        if digest != DIGESTS_AT_7389ABA[config]:
            print(f"  ERROR {config}: digest {digest[:12]} is not the pinned {DIGESTS_AT_7389ABA[config][:12]}.")
            print("        This machine does not reproduce the pins, so its values are not the reference.")
            return 1
        out[config] = {"inputs": plan.inputs, "tiers": plan.tiers, "input_digest": digest}
        print(f"  wrote {config} ({digest[:12]})")

    # No `default=`: everything here is JSON-native, and json already writes floats as repr does, so they
    # round-trip exactly. A type that needs coercing is a change worth failing on rather than stringifying.
    OUT.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(f"{OUT.relative_to(ROOT)}: {len(out)} configs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
