"""Pre-staging checks on PDB text: chains, HETATM count, missing residues, parse warnings. Pure; PDB only."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class StructureCheck:
    chains: dict[str, int]  # chain id -> residue count (ATOM records)
    n_hetatm: int
    missing_residues: int | None  # REMARK 465 rows; None when the file has no such block
    possible_gaps: list[str]  # "A 45-47": sequence numbers absent inside a chain
    warnings: list[str] = field(default_factory=list)


_SSSEQ = re.compile(r"^-?\d+[A-Z]?$")


def _close(gaps: list[str], chain: str, run: list[int]) -> None:
    if not run or run == [0]:  # numbering -1 -> 1 skips 0 by convention
        return
    gaps.append(f"{chain} {run[0]}-{run[-1]}" if len(run) > 1 else f"{chain} {run[0]}")


def check_pdb(text: str) -> StructureCheck:
    residues: dict[str, list[int]] = {}
    seen: set[tuple[str, int, str]] = set()
    n_het = 0
    bad = 0
    altlocs = 0
    n465: int | None = None
    in465 = False
    het_seq: dict[str, set[int]] = {}
    for line in text.splitlines():
        if line.startswith("ENDMDL"):  # NMR ensembles repeat the model; count the first only
            break
        if line.startswith("REMARK 465"):
            parts = line[10:].split()
            # data rows are "<resname> <chain> <seq>"; the header and legend lines fail the int test
            if len(parts) >= 3 and _SSSEQ.match(parts[-1]) and len(parts[0]) <= 4 and in465:
                n465 = (n465 or 0) + 1
            elif "RES C SSSEQ" in line:
                in465 = True
                n465 = n465 or 0
            continue
        if not line.startswith(("ATOM", "HETATM")):
            continue
        if line.startswith("HETATM"):
            n_het += 1
        try:
            seq = int(line[22:26])
            float(line[30:38]), float(line[38:46]), float(line[46:54])
        except ValueError:
            bad += 1
            continue
        if line[16:17].strip():
            altlocs += 1
        if line.startswith("HETATM"):
            het_seq.setdefault(line[21:22].strip() or "_", set()).add(seq)  # e.g. MSE inside the chain
        if line.startswith("ATOM"):
            key = (line[21:22].strip() or "_", seq, line[26:27])
            if key not in seen:
                seen.add(key)
                residues.setdefault(key[0], []).append(seq)
    chains = {c: len(v) for c, v in residues.items()}
    gaps: list[str] = []
    for c, seqs in residues.items():
        have = sorted(set(seqs))
        occupied = set(have) | het_seq.get(c, set())
        run: list[int] = []
        for n in range(have[0], have[-1] + 1):
            if n not in occupied:
                run.append(n)
                continue
            _close(gaps, c, run)
            run = []
        _close(gaps, c, run)
    warnings = []
    if bad:
        warnings.append(f"{bad} ATOM/HETATM line(s) failed to parse (non-numeric residue number or coordinates)")
    if altlocs:
        warnings.append(f"{altlocs} atom record(s) carry an alternate-location flag; only one conformer is used")
    return StructureCheck(chains, n_het, n465, gaps, warnings)
