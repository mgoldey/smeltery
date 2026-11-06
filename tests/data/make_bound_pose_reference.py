"""Regenerate tests/data/bound_pose_reference.json: the danuglipron example's own pairing, as it was at 720e4ca.

The reference is the example's `make_pairing(bound_pose_in_smiles_order(xyz), N_POSES)` run on the parent and one
analogue (bzim-C9-F: its exact MCS takes about 2 s), with the example module taken from `git show 720e4ca:...` (the
commit before `[bound_pose]` moved that code into `smeltery.methods.bound_pose`), so the test compares the method to
the code it replaced and not to itself. Coordinates are written with `repr` floats, so they round-trip exactly.

Usage, from the repository root: .venv/bin/python tests/data/make_bound_pose_reference.py
Not collected by pytest.
"""

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMMIT = "720e4ca"
XYZ = ROOT / "src" / "smeltery" / "prebuilt" / "danuglipron-halogen" / "conf_00_cryo_em.xyz"
ANALOGUE = "bzim-C9-F"
N_POSES = 4


def main() -> None:
    src = subprocess.run(
        ["git", "show", f"{COMMIT}:examples/danuglipron_halogen.py"], cwd=ROOT, check=True, capture_output=True
    ).stdout
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "danuglipron_halogen_at_720e4ca.py"
        path.write_bytes(src)
        spec = importlib.util.spec_from_file_location("danuglipron_halogen_at_720e4ca", path)
        assert spec is not None and spec.loader is not None
        ex = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = ex
        spec.loader.exec_module(ex)
    parent = ex.Candidate("parent", ex.PARENT_SMILES)
    ana = ex.Candidate(ANALOGUE, ex.analogue_smiles()[ANALOGUE])
    pairing = ex.make_pairing(ex.bound_pose_in_smiles_order(XYZ), N_POSES)
    pairing.run([parent, ana], {"parent": parent})
    out = {
        "made_by": f"tests/data/make_bound_pose_reference.py: examples/danuglipron_halogen.py at {COMMIT}, "
        f"make_pairing(bound_pose_in_smiles_order(conf_00_cryo_em.xyz), {N_POSES}) on the parent and {ANALOGUE}",
        "settings": pairing.settings(),
        "candidates": {
            c.name: {
                "smiles": c.smiles,
                "symbols": list(c.poses[0].symbols),
                "poses": [[[float(v) for v in row] for row in p.coords_ang] for p in c.poses],
            }
            for c in (parent, ana)
        },
    }
    dest = Path(__file__).with_name("bound_pose_reference.json")
    dest.write_text(json.dumps(out, indent=None, separators=(",", ":")) + "\n")
    print(f"wrote {dest}")


if __name__ == "__main__":
    main()
