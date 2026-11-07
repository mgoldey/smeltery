"""Reading candidates from a CSV or SDF and writing them back out. Pure in the text it is given: the routes own
the draft, the uploads and the downloads.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

from rdkit import Chem
from rdkit.Chem import rdDepictor

SMILES_COLUMNS = ("smiles", "canonical_smiles")
NAME_COLUMNS = ("name", "id", "title")


@dataclass(frozen=True)
class Row:
    row: int  # 1-based: the data row of a CSV, the record of an SDF
    name: str | None
    smiles: str


@dataclass(frozen=True)
class Rejected:
    row: int
    input: str
    reason: str


def parse_csv(text: str) -> tuple[list[Row], list[Rejected]]:
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    header = next(reader, None)
    if header is None:
        return [], [Rejected(0, "", "the file is empty")]
    lower = [h.strip().lower() for h in header]
    smi = next((lower.index(c) for c in SMILES_COLUMNS if c in lower), None)
    if smi is None:
        return [], [Rejected(0, ",".join(header), "no SMILES column (expected smiles or canonical_smiles)")]
    nm = next((lower.index(c) for c in NAME_COLUMNS if c in lower), None)
    rows: list[Row] = []
    bad: list[Rejected] = []
    for fields in reader:
        if not any(f.strip() for f in fields):
            continue
        smiles = fields[smi].strip() if smi < len(fields) else ""
        name = fields[nm].strip() if nm is not None and nm < len(fields) else ""
        rows.append(Row(reader.line_num, name or None, smiles))
    return rows, bad


def parse_sdf(text: str) -> tuple[list[Row], list[Rejected]]:
    sup = Chem.SDMolSupplier()
    sup.SetData(text, sanitize=True, removeHs=True)
    rows: list[Row] = []
    bad: list[Rejected] = []
    for i, mol in enumerate(sup, start=1):
        if mol is None:
            bad.append(Rejected(i, f"record {i}", "RDKit cannot read this SDF record"))
            continue
        name = next(
            (v for k in ("_Name", *NAME_COLUMNS, "Name", "ID") if mol.HasProp(k) and (v := mol.GetProp(k).strip())), ""
        )
        rows.append(Row(i, name or None, Chem.MolToSmiles(mol)))
    return rows, bad


def parse(filename: str, text: str) -> tuple[list[Row], list[Rejected]]:
    low = filename.lower()
    if low.endswith(".sdf"):
        return parse_sdf(text)
    if low.endswith(".csv"):
        return parse_csv(text)
    return [], [Rejected(0, filename, "only .csv and .sdf files can be imported")]


def accept(
    rows: list[Row], rejected: list[Rejected], existing: dict[str, str]
) -> tuple[dict[str, str], list[Rejected]]:
    """The new {name: smiles} to append, and every row refused with its reason. A name or a molecule (compared by
    canonical SMILES) already among `existing`, or earlier in the file, is refused, not renamed."""
    seen_names = set(existing)
    seen_smiles = {_canon(s) for s in existing.values()} - {None}
    new: dict[str, str] = {}
    out = list(rejected)
    for r in rows:
        name = r.name or _fresh(seen_names, f"imported_{r.row}")
        canon = _canon(r.smiles)
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
            reason = "the name contains a control character"
        elif canon is None:
            reason = "no SMILES" if not r.smiles else "RDKit cannot read the SMILES"
        elif name in seen_names:
            reason = f"a candidate named {name!r} already exists"
        elif canon in seen_smiles:
            reason = "this molecule is already a candidate"
        else:
            seen_names.add(name)
            seen_smiles.add(canon)
            new[name] = r.smiles
            continue
        out.append(Rejected(r.row, f"{r.name or ''},{r.smiles}".lstrip(","), reason))
    return new, sorted(out, key=lambda x: x.row)


def _canon(smiles: str) -> str | None:
    mol = Chem.MolFromSmiles(smiles) if smiles.strip() else None
    return None if mol is None else Chem.MolToSmiles(mol)


def _fresh(taken: set[str], base: str) -> str:
    name, k = base, 2
    while name in taken:
        name, k = f"{base}_{k}", k + 1
    return name


#: What an export carries beyond name and SMILES, in column order.
EXPORT_PROPS = ("role", "formal_charge", "electrons", "electrons_even", "sto3g_ok")


def to_csv(records: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(("name", "smiles", *EXPORT_PROPS))
    for r in records:
        w.writerow([r["name"], r["smiles"], *("" if r[p] is None else r[p] for p in EXPORT_PROPS)])
    return buf.getvalue()


def to_sdf(records: list[dict]) -> str:
    buf = io.StringIO()
    w = Chem.SDWriter(buf)
    for r in records:
        mol = Chem.MolFromSmiles(r["smiles"]) if r["smiles"].strip() else None
        if mol is None:
            continue  # an unreadable SMILES has no structure to write; the CSV still lists it
        rdDepictor.Compute2DCoords(mol)
        mol.SetProp("_Name", r["name"])
        mol.SetProp("smiles", r["smiles"])
        for p in EXPORT_PROPS:
            if r[p] is not None:
                mol.SetProp(p, str(r[p]))
        w.write(mol)
    w.close()
    return buf.getvalue()
