"""Golden path helpers: scaffold a config, open a run everywhere, bundle it for an ELN.

These are convenience wrappers around the existing `run` / `gui` / `avogadro` / `report` commands.
They encode the recommended end-to-end flow so an end user (or an ELN integration) has one
command per step instead of remembering flags.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import zipfile
from pathlib import Path

TEMPLATE = """# smeltery golden-path config (edit me)
campaign = "{campaign}"
parent = "{parent}"

[candidates]
{parent} = "{parent_smiles}"
# analogue = "SMILES"

[poses]
n_poses = 6

[[pocket.site]]
q = 1.0
atom = 1
from_atom = 2
distance = 3.0

[cut]
keep = 1
z = 2.0
floor = 0.0
"""


def cmd_init(argv):
    ap = argparse.ArgumentParser(prog="smeltery init")
    ap.add_argument("--out", default="smeltery.toml")
    ap.add_argument("--campaign", default="demo")
    ap.add_argument("--parent", default="benzoic")
    ap.add_argument("--parent-smiles", default="OC(=O)c1ccccc1")
    a = ap.parse_args(argv)
    path = Path(a.out)
    if path.exists():
        print(f"{path} already exists; not overwriting", file=sys.stderr)
        return 1
    path.write_text(TEMPLATE.format(campaign=a.campaign, parent=a.parent, parent_smiles=a.parent_smiles))
    print(f"wrote {path}")
    print("next: edit candidates, then `smeltery run {0} --plan`".format(path))
    return 0


def cmd_open(argv):
    ap = argparse.ArgumentParser(prog="smeltery open")
    ap.add_argument("out", help="run OUT directory or run-*.json")
    ap.add_argument("--no-gui", action="store_true")
    ap.add_argument("--no-avogadro", action="store_true")
    ap.add_argument("--report", action="store_true", help="also write report.html if missing")
    a = ap.parse_args(argv)
    out = Path(a.out)
    if a.report:
        rep = out / "report.html" if out.is_dir() else out.parent / "report.html"
        if not rep.exists():
            subprocess.call([sys.executable, "-m", "smeltery.cli", "report", str(a.out), "-o", str(rep)])
    if not a.no_gui:
        subprocess.Popen(
            [sys.executable, "-m", "smeltery.cli", "gui", str(a.out)],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    if not a.no_avogadro:
        try:
            subprocess.Popen(
                [sys.executable, "-m", "smeltery.cli", "avogadro", str(a.out)],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:  # noqa: BLE001
            print(f"avogadro launch failed: {e}", file=sys.stderr)
    print(f"opened {a.out}")
    return 0


def cmd_bundle(argv):
    ap = argparse.ArgumentParser(prog="smeltery bundle")
    ap.add_argument("out", help="run OUT directory or run-*.json")
    ap.add_argument("-o", "--output", default=None, help="zip path (default: OUT/bundle.zip)")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out_dir = out if out.is_dir() else out.parent
    zip_path = Path(a.output) if a.output else (out_dir / "bundle.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for p in out_dir.rglob("*"):
            if p.is_file() and p.resolve() != zip_path.resolve():
                z.write(p, p.relative_to(out_dir))
    print(f"bundled {out_dir} -> {zip_path}")
    return 0


def main(argv=None) -> int:
    argv = list(argv or [])
    if not argv or argv[0] not in {"init", "open", "bundle"}:
        print("usage: smeltery golden {init|open|bundle} ...", file=sys.stderr)
        return 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "init":
        return cmd_init(rest)
    if cmd == "open":
        return cmd_open(rest)
    if cmd == "bundle":
        return cmd_bundle(rest)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
