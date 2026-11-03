"""Integration with Avogadro 2 for visualizing smeltery runs.

Avogadro 2 is a desktop molecular editor/viewer. This module does two things:

1. Export a finished run's geometry (receptor + per-candidate pose SDFs) into one directory
   Avogadro can open, with a short index file.
2. Launch Avogadro 2 on that directory/file. Avogadro is discovered via ``PATH`` first, then
   the Flatpak install used on this machine.

No Avogadro library is required: the integration is file-based, so it works with any
Avogadro 2 build that can read SDF/PDB.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def avogadro_command(path: str | Path | None = None) -> list[str]:
    """The command to launch Avogadro 2, optionally opening ``path``."""
    if shutil.which("avogadro2"):
        cmd = ["avogadro2"]
    elif shutil.which("flatpak"):
        try:
            subprocess.run(
                ["flatpak", "info", "org.openchemistry.Avogadro2"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            raise FileNotFoundError("Avogadro 2 Flatpak (org.openchemistry.Avogadro2) is not installed") from e
        cmd = ["flatpak", "run", "org.openchemistry.Avogadro2"]
    else:
        raise FileNotFoundError("Avogadro 2 not found (no 'avogadro2' on PATH and no 'flatpak')")
    if path is not None:
        cmd.append(str(path))
    return cmd


def launch_avogadro(path: str | Path | None = None) -> subprocess.Popen:
    """Launch Avogadro 2 without blocking. Returns the Popen handle."""
    cmd = avogadro_command(path)
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return proc


def export_run(run, dest: str | Path) -> Path:
    """Copy a run's receptor and pose SDFs into ``dest``. Returns ``dest``."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    out_dir = Path(run.out_dir)
    results = run.record["results"]

    receptor = results.get("receptor")
    receptor_file = None
    if isinstance(receptor, dict):
        receptor_file = receptor.get("file")
    elif isinstance(receptor, str):
        receptor_file = receptor
    if receptor_file:
        src = out_dir / receptor_file
        if src.exists():
            (dest / "receptor.pdb").write_bytes(src.read_bytes())

    poses = results.get("poses") or {}
    for name, rel in poses.items():
        src = out_dir / rel
        if src.exists():
            safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
            (dest / f"{safe}.sdf").write_bytes(src.read_bytes())

    # field charges as a simple XYZ (Avogadro can open it; charges in the comment line)
    field = results.get("field")
    if field:
        lines = [str(len(field)), "smeltery pocket field charges (charges are in the run record; positions only here)"]
        for _q, x, y, z in field:
            # Avogadro needs an element; use a dummy 'X' with the charge in the name is not
            # ideal, but keeps the file openable. The provenance JSON is authoritative.
            lines.append(f"X {x:.6f} {y:.6f} {z:.6f}")
        (dest / "field.xyz").write_text("\n".join(lines) + "\n")

    index = dest / "README.txt"
    index.write_text(
        f"smeltery run: {run.campaign}\n"
        f"parent: {run.parent}\n"
        f"candidates: {', '.join(run.candidates)}\n"
        f"survivors: {run.survivors}\n"
        f"unranked: {run.unranked}\n"
        "\nOpen any .sdf / .pdb / .xyz in this directory with Avogadro 2.\n"
    )
    return dest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="smeltery avogadro", description="Export a run and open it in Avogadro 2.")
    ap.add_argument("out", help="run OUT directory or run-*.json")
    ap.add_argument("--dest", default=None, help="export directory (default: OUT/avogadro)")
    ap.add_argument("--no-launch", action="store_true", help="export only, do not launch Avogadro 2")
    ap.add_argument("--path", default=None, help="specific file to open (default: first pose SDF)")
    args = ap.parse_args(argv)

    from smeltery.gui.results import load_run

    run = load_run(args.out)
    out_dir = Path(args.out) if Path(args.out).is_dir() else Path(args.out).parent
    dest = Path(args.dest) if args.dest else (out_dir / "avogadro")
    export_run(run, dest)
    print(f"exported to {dest}")

    if args.no_launch:
        return 0
    target = args.path
    if target is None:
        sdf = sorted(dest.glob("*.sdf"))
        target = sdf[0] if sdf else (dest / "receptor.pdb" if (dest / "receptor.pdb").exists() else None)
    if target is None:
        print("nothing to open", file=sys.stderr)
        return 1
    try:
        launch_avogadro(target)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 2
    print(f"launched Avogadro 2 on {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
