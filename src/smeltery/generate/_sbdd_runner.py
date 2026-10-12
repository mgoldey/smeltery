"""Run DiffSBDD scaffold inpainting. Executed by the EXTERNAL Python (the one with torch and DiffSBDD installed).

    <python> _sbdd_runner.py request.json

smeltery never imports this file, and it imports nothing from smeltery: it must run under Python 3.10 with
torch 2.0.1, outside the smeltery environment. Top-level imports are stdlib only; torch, RDKit and DiffSBDD are
imported inside `run`, so the request and report plumbing can be tested without them.

Two things in DiffSBDD's `inpaint.py` are fixed at runtime rather than by editing the checkout, so its commit
hash stays the upstream one. Both are listed under "patches" in the report:

* `utils.batch_to_list` indexes a CPU tensor with a CUDA mask and fails on GPU after sampling;
* `inpaint.prepare_substructure` reads a module global `model` that exists only when `inpaint.py` runs as a script.

Exit status: 0 success, 1 the run failed (the report says why), 2 bad command line or request.
"""

import hashlib
import json
import random
import subprocess
import sys
import time
import traceback
from pathlib import Path

REQUIRED = (
    "diffsbdd_dir",
    "checkpoint",
    "pdb",
    "ref_ligand",
    "fix_atoms",
    "out_sdf",
    "out_report",
    "n_samples",
    "timesteps",
    "resamplings",
    "center",
    "seed",
)


def load_request(path):
    request = json.loads(Path(path).read_text())
    missing = [k for k in REQUIRED if k not in request]
    if missing:
        raise ValueError(f"request is missing {missing}")
    return request


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_info(repo):
    """(HEAD commit or None, whether a TRACKED file differs from HEAD). Untracked files do not count."""

    def git(*args):
        try:
            return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
        except OSError:
            return None

    head = git("rev-parse", "HEAD")
    if head is None or head.returncode != 0:
        return None, False
    status = git("status", "--porcelain", "--untracked-files=no")
    return head.stdout.strip(), bool(status is not None and status.stdout.strip())


def run(request):
    started = time.time()
    repo = Path(request["diffsbdd_dir"])
    own_dir = str(Path(__file__).resolve().parent)
    if sys.path and sys.path[0] == own_dir:
        del sys.path[
            0
        ]  # run by path, this directory (smeltery's model.py, base.py ...) would shadow DiffSBDD's modules
    sys.path.insert(0, str(repo))
    import numpy as np
    import torch
    from rdkit import Chem

    seed = int(request["seed"])
    random.seed(seed)
    np.random.seed(seed % 2**32)
    torch.manual_seed(seed)

    import inpaint
    import utils
    from lightning_modules import LigandPocketDDPM

    patches = []
    original_batch_to_list = utils.batch_to_list

    def batch_to_list(data, batch_mask):
        return original_batch_to_list(data, batch_mask.to(data.device))

    utils.batch_to_list = batch_to_list
    patches.append("utils.batch_to_list: batch_mask moved to data.device")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = LigandPocketDDPM.load_from_checkpoint(request["checkpoint"], map_location=device).to(device)
    inpaint.model = model
    patches.append("inpaint.model: prepare_substructure reads this module global")

    molecules = inpaint.inpaint_ligand(
        model,
        request["pdb"],
        int(request["n_samples"]),
        request["ref_ligand"],
        [request["fix_atoms"]],
        add_n_nodes=request.get("add_n_nodes"),
        center=request["center"],
        sanitize=False,
        largest_frag=False,
        relax_iter=0,
        timesteps=int(request["timesteps"]),
        resamplings=int(request["resamplings"]),
    )
    writer = Chem.SDWriter(str(request["out_sdf"]))
    writer.SetKekulize(False)  # the molecules are unsanitized
    for molecule in molecules:
        writer.write(molecule)
    writer.close()

    commit, dirty = git_info(repo)
    return {
        "ok": True,
        "n_raw": len(molecules),
        "wall_time_s": round(time.time() - started, 3),
        "seed": seed,
        "checkpoint_sha256": sha256_of(request["checkpoint"]),
        "diffsbdd_commit": commit,
        "diffsbdd_dirty": dirty,
        "torch": torch.__version__,
        "cuda": torch.version.cuda,
        "device": torch.cuda.get_device_name(0) if device == "cuda" else "cpu",
        "cuda_free_mib": round(torch.cuda.mem_get_info()[0] / 2**20) if device == "cuda" else None,
        "patches": patches,
    }


def free_gpu_mib():
    """Free memory on the current CUDA device in MiB, or None; never raises (it runs while reporting a failure)."""
    try:
        import torch

        if torch.cuda.is_available():
            return round(torch.cuda.mem_get_info()[0] / 2**20)
    except BaseException:  # noqa: BLE001 - the failure report must still be written
        pass
    return None


def main(argv):
    if len(argv) != 2:
        print("usage: _sbdd_runner.py request.json", file=sys.stderr)
        return 2
    try:
        request = load_request(argv[1])
    except Exception as exc:  # noqa: BLE001 - any unreadable request is a usage error
        print(f"bad request: {exc}", file=sys.stderr)
        return 2
    try:
        report, status = run(request), 0
    except BaseException:  # noqa: BLE001 - CUDA out-of-memory and import errors must reach the report too
        report, status = {"ok": False, "error": traceback.format_exc(), "cuda_free_mib": free_gpu_mib()}, 1
    Path(request["out_report"]).write_text(json.dumps(report, indent=2, sort_keys=True))
    if status:
        print(report["error"], file=sys.stderr)
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv))
