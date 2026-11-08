"""Pre-staging checks on PDB text: chains, HETATM count, missing residues, parse warnings. Pure; PDB only."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class StructureCheck:
    chains: dict[str, int]  # chain id -> residue count (ATOM records)
    n_hetatm: int
    missing_residues: int | None  # REMARK 465 rows; None when the file has no such block
    possible_gaps: list[str]  # "A 45-47": sequence numbers absent inside a chain
    warnings: list[str] = field(default_factory=list)


def check_pdb(text: str) -> StructureCheck:
    residues: dict[str, list[int]] = {}
    seen: set[tuple[str, int, str]] = set()
    n_het = 0
    bad = 0
    altlocs = 0
    n465: int | None = None
    in465 = False
    for line in text.splitlines():
        if line.startswith("REMARK 465"):
            parts = line[10:].split()
            # data rows are "<resname> <chain> <seq>"; the header and legend lines fail the int test
            if len(parts) >= 3 and parts[-1].lstrip("-").isdigit() and len(parts[0]) <= 4 and in465:
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
        if line.startswith("ATOM"):
            key = (line[21:22].strip() or "_", seq, line[26:27])
            if key not in seen:
                seen.add(key)
                residues.setdefault(key[0], []).append(seq)
    chains = {c: len(v) for c, v in residues.items()}
    gaps: list[str] = []
    for c, seqs in residues.items():
        have = sorted(set(seqs))
        for a, b in zip(have, have[1:]):
            if b - a > 1:
                gaps.append(f"{c} {a + 1}-{b - 1}" if b - a > 2 else f"{c} {a + 1}")
    warnings = []
    if bad:
        warnings.append(f"{bad} ATOM/HETATM line(s) failed to parse (non-numeric residue number or coordinates)")
    if altlocs:
        warnings.append(f"{altlocs} atom record(s) carry an alternate-location flag; only one conformer is used")
    return StructureCheck(chains, n_het, n465, gaps, warnings)
