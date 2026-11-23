"""Train / valid / test splits of an exported dataset, kept as small versioned JSON artifacts.

`smeltery export` writes one table (docs/dataset-export.md). A split is a file that says which ROWS of that table
belong to which partition, so a model trained today and one trained next year see the same rows, and a table that was
re-exported with different contents cannot be silently paired with an old split.

A split file records: the split `kind` (`scaffold` or `target`), `algorithm_version` (bumped when the assignment rule
changes) and `schema_version` (the file layout), the `seed`, the `parameters` (fractions and the like), the source
table's SHA-256, and the identity of every row in each partition.

* **Row identity** is `[run_id, ligand, target, tier]`, the export's grain (`dataset_export.GRAIN`). It is unique in
  an export (a run twice is refused there) and does not depend on row order or on any score.
* **Table digest** is the SHA-256 of the table FILE's bytes, not of the row identities. Identities alone would let a
  re-export with changed scores pair with a split made on the old numbers; the file digest changes on any change to
  any value, and anyone can check it with `sha256sum`. The price: the same content written by a different pyarrow
  version can have a different digest, and the split then refuses until it is re-made. That is deliberate: the split
  names the file it was made on.
* **Groups never straddle partitions.** A scaffold split puts every row of a Bemis-Murcko scaffold in one partition;
  a target split puts every row of a target in one. Groups are put in order by a seeded shuffle of their sorted keys
  and assigned greedily so each partition reaches its row fraction; a partition left with no rows is refused rather
  than written empty.
* **Scaffold needs RDKit and refuses without it.** A different grouping from a fallback would be a different split
  under the same name. A molecule RDKit cannot parse is refused for the same reason. Acyclic molecules have the empty
  scaffold and form one group.
* **Deterministic.** The same table and seed give a byte-identical file: sorted keys, no timestamps, no paths.

* **A dataset directory is immutable** (`smeltery export dataset`, smeltery.dataset_record). `write_split` refuses a
  destination inside one, and `smeltery export split KIND DIR -o NEWDIR` makes a NEW version of the dataset with the
  split added, leaving DIR as it was, so a run trained on DIR's content digest still resolves. `verify SPLIT DIR`
  checks the directory against its record first, then the split (a path, or a split name the record lists) against
  the table inside it.

Computing a split needs pyarrow (to read the table) and, for scaffolds, RDKit; never torch.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from .record import file_sha256

SCHEMA_VERSION = 1
FORMAT = "smeltery.split"
#: Bump when the rule that turns (table, seed, parameters) into partitions changes.
ALGORITHM_VERSIONS = {"scaffold": 1, "target": 1}
KINDS = tuple(ALGORITHM_VERSIONS)
IDENTITY_COLUMNS = ("run_id", "ligand", "target", "tier")
DEFAULT_FRACTIONS = {"train": 0.8, "valid": 0.1, "test": 0.1}


class SplitError(ValueError):
    """A split cannot be made, or does not belong to the table it is loaded against."""


table_sha256 = file_sha256  # the one file hash, `smeltery.record.file_sha256`


def read_table(path: str | Path):
    """The exported table as a pyarrow Table (Parquet or Arrow IPC file, told apart by magic bytes)."""
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as e:
        raise SplitError("reading a dataset needs pyarrow, the optional `dataset` extra") from e
    p = Path(path)
    try:
        with open(p, "rb") as f:
            magic = f.read(6)
    except OSError as e:
        raise SplitError(f"cannot read {p}: {e}") from e
    if magic[:4] == b"PAR1":
        return pq.read_table(p)
    if magic == b"ARROW1":
        return pa.ipc.open_file(p).read_all()
    raise SplitError(f"{p} is neither a Parquet nor an Arrow IPC file")


def identities(table) -> list[tuple[str, str, str, str]]:
    missing = [c for c in IDENTITY_COLUMNS if c not in table.column_names]
    if missing:
        raise SplitError(f"table lacks identity column(s) {missing}: not a smeltery export")
    cols = [table.column(c).to_pylist() for c in IDENTITY_COLUMNS]
    ids = list(zip(*cols, strict=True))
    if len(set(ids)) != len(ids):
        raise SplitError("row identities (run_id, ligand, target, tier) are not unique in this table")
    return ids


def _scaffold(smiles: str, generic: bool) -> str:
    try:
        from rdkit import Chem
        from rdkit.Chem.Scaffolds import MurckoScaffold
    except ImportError as e:
        raise SplitError(
            "a scaffold split needs RDKit and will not fall back to another grouping (install the `rdkit` extra)"
        ) from e
    mol = Chem.MolFromSmiles(smiles) if isinstance(smiles, str) else None
    if mol is None:
        raise SplitError(f"RDKit cannot parse SMILES {smiles!r}: refusing to guess its scaffold")
    core = MurckoScaffold.GetScaffoldForMol(mol)
    if generic:
        core = MurckoScaffold.MakeScaffoldGeneric(core)
    return Chem.MolToSmiles(core)


def _fractions(fractions: dict[str, float] | None) -> dict[str, float]:
    fr = dict(DEFAULT_FRACTIONS if fractions is None else fractions)
    if len(fr) < 2 or any(not isinstance(v, (int, float)) or v <= 0 for v in fr.values()):
        raise SplitError(f"fractions must name at least two partitions with positive values, got {fr}")
    total = sum(fr.values())
    if abs(total - 1.0) > 1e-9:
        raise SplitError(f"fractions must sum to 1, got {total}")
    return fr


def assign_groups(sizes: dict[str, int], fractions: dict[str, float], seed: int) -> dict[str, str]:
    """Partition name for each group key. Pure: keys are shuffled by `seed` after sorting, then each partition but
    the last takes groups until it holds its fraction of the rows (at least one group), leaving one group for each
    later partition; the last takes the rest."""
    names = list(fractions)
    keys = sorted(sizes)
    if len(keys) < len(names):
        raise SplitError(f"{len(keys)} group(s) cannot fill {len(names)} partitions {names}")
    random.Random(seed).shuffle(keys)
    total = sum(sizes.values())
    out: dict[str, str] = {}
    i = 0
    for pi, name in enumerate(names):
        if pi == len(names) - 1:
            take = len(keys) - i
        else:
            want, held, take = fractions[name] * total, 0, 0
            reserve = len(names) - 1 - pi
            while i + take < len(keys) - reserve and (take == 0 or held < want):
                held += sizes[keys[i + take]]
                take += 1
        for k in keys[i : i + take]:
            out[k] = name
        i += take
    return out


def make_split(
    table_path: str | Path,
    kind: str,
    *,
    seed: int = 0,
    fractions: dict[str, float] | None = None,
    smiles_column: str = "smiles",
    generic: bool = False,
) -> dict:
    """The split document for the table at `table_path` (not yet written)."""
    if kind not in KINDS:
        raise SplitError(f"unknown split kind {kind!r}; known: {', '.join(KINDS)}")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise SplitError(f"seed must be an integer, got {seed!r}")
    fr = _fractions(fractions)
    table = read_table(table_path)
    ids = identities(table)
    if kind == "scaffold":
        if smiles_column not in table.column_names:
            raise SplitError(f"table has no column {smiles_column!r}")
        smiles = table.column(smiles_column).to_pylist()
        cache: dict[str, str] = {}
        for sm in smiles:
            if sm not in cache:
                cache[sm] = _scaffold(sm, generic)
        group_of = [cache[sm] for sm in smiles]
        params = {"fractions": fr, "generic": generic, "smiles_column": smiles_column}
    else:
        group_of = [i[2] for i in ids]
        params = {"fractions": fr, "group_column": "target"}
    sizes: dict[str, int] = {}
    for g in group_of:
        sizes[g] = sizes.get(g, 0) + 1
    part_of = assign_groups(sizes, fr, seed)
    partitions: dict[str, list[list[str]]] = {name: [] for name in fr}
    for ident, g in zip(ids, group_of, strict=True):
        partitions[part_of[g]].append(list(ident))
    for name in partitions:
        partitions[name].sort()
    return {
        "format": FORMAT,
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "algorithm_version": ALGORITHM_VERSIONS[kind],
        "seed": seed,
        "parameters": params,
        "table": {"sha256": table_sha256(table_path), "rows": len(ids), "identity_columns": list(IDENTITY_COLUMNS)},
        "partitions": partitions,
    }


def dumps(split: dict) -> str:
    """The one serialisation: sorted keys, fixed indent, trailing newline. Byte-identical for equal documents."""
    return json.dumps(split, sort_keys=True, indent=1, ensure_ascii=False) + "\n"


def write_split(split: dict, dest: str | Path) -> None:
    """Write `split` to `dest`. Refused inside a dataset directory, which is immutable once its record exists."""
    from .dataset_record import inside_dataset_dir

    root = inside_dataset_dir(Path(dest).parent)
    if root is not None:
        raise SplitError(
            f"{dest} is inside the dataset directory {root}, which is immutable; "
            f"`smeltery export split KIND {root} -o NEWDIR` writes a new version with the split"
        )
    Path(dest).write_text(dumps(split), encoding="utf-8")


def load_split(path: str | Path, table_path: str | Path) -> dict:
    """Read a split file and check it against the table: the table's digest must equal the recorded one, and every
    row of the table must sit in exactly one partition. Raises SplitError on any mismatch."""
    try:
        split = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SplitError(f"cannot read split {path}: {e}") from e
    if not isinstance(split, dict) or split.get("format") != FORMAT:
        raise SplitError(f"{path} is not a smeltery split file")
    if split.get("schema_version") != SCHEMA_VERSION:
        raise SplitError(f"split schema_version {split.get('schema_version')!r}, this smeltery reads {SCHEMA_VERSION}")
    kind = split.get("kind")
    if kind not in ALGORITHM_VERSIONS:
        raise SplitError(f"unknown split kind {kind!r}")
    if split.get("algorithm_version") != ALGORITHM_VERSIONS[kind]:
        raise SplitError(
            f"{kind} split algorithm_version {split.get('algorithm_version')!r}; this smeltery's is "
            f"{ALGORITHM_VERSIONS[kind]}. The recorded partitions are still valid; remake it to reproduce them."
        )
    actual = table_sha256(table_path)
    want = (split.get("table") or {}).get("sha256")
    if actual != want:
        raise SplitError(
            f"split {Path(path).name} was made on a table with sha256 {want}, but {Path(table_path).name} is {actual}: "
            "the table changed since the split was made"
        )
    ids = identities(read_table(table_path))
    seen: dict[tuple, str] = {}
    for name, rows in split["partitions"].items():
        for r in rows:
            t = tuple(r)
            if t in seen:
                raise SplitError(f"row {t} is in partitions {seen[t]!r} and {name!r}")
            seen[t] = name
    if set(seen) != set(ids):
        raise SplitError("split rows and table rows differ although the digest matches: the split file was edited")
    return split


def partition_indices(split: dict, table_path: str | Path) -> dict[str, list[int]]:
    """Row positions in the table for each partition (table order). Call after `load_split` has validated."""
    pos = {ident: n for n, ident in enumerate(identities(read_table(table_path)))}
    return {name: sorted(pos[tuple(r)] for r in rows) for name, rows in split["partitions"].items()}


def main(argv: list[str] | None = None) -> int:
    """`smeltery export split KIND TABLE -o FILE [--seed N]`; `verify FILE TABLE` checks an existing one. TABLE may be a
    dataset directory: then KIND writes a new version of it into the directory `-o` (`--version` labels it), and
    `verify` takes a split file or the name of a split the directory lists."""
    import argparse
    import sys

    from . import dataset_record as DR

    ap = argparse.ArgumentParser(
        prog="smeltery export split",
        description="Write a versioned split artifact (JSON) for an exported table, or verify one against a table.",
    )
    ap.add_argument("kind", choices=(*KINDS, "verify"), help="scaffold or target; `verify SPLIT TABLE` checks a file")
    ap.add_argument("table", help="the exported .parquet/.arrow (for `verify`: the split file, then the table)")
    ap.add_argument("extra", nargs="?", help=argparse.SUPPRESS)
    ap.add_argument("-o", "--out", help="the split file to write; for a dataset directory, the new version's directory")
    ap.add_argument("--version", help="dataset directory only: the new version's label (default: the old one + 1)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--fractions", help="e.g. train=0.8,valid=0.1,test=0.1 (the default)")
    ap.add_argument("--smiles-column", default="smiles")
    ap.add_argument("--generic", action="store_true", help="scaffold: group by the generic (atom-type-free) scaffold")
    args = ap.parse_args(argv)
    try:
        if args.kind == "verify":
            if args.extra is None:
                ap.error("verify needs SPLIT and TABLE")
            split_path, table = args.table, args.extra
            if DR.is_dataset_dir(table):
                opened = DR.open_dataset(table)
                if not Path(split_path).is_file():
                    split_path = opened.split(split_path)[1]
                table = opened.table
            load_split(split_path, table)
            print(f"{args.table} matches {args.extra}")
            return 0
        if not args.out:
            ap.error("-o/--out is required")
        fr = DR.parse_fractions(args.fractions)
        if DR.is_dataset_dir(args.table):
            spec = DR.SplitSpec(args.kind, args.seed, fr, args.generic, args.smiles_column)
            out = DR.new_version(args.table, args.out, split=spec, version=args.version)
            print(f"wrote {spec.name} into a new version of {args.table}: {out}")
            return 0
        if args.version is not None:
            ap.error("--version applies to a dataset directory only")
        split = make_split(
            args.table, args.kind, seed=args.seed, fractions=fr, smiles_column=args.smiles_column, generic=args.generic
        )
        write_split(split, args.out)
    except (SplitError, ValueError) as e:  # DatasetError is an ExportError, a ValueError
        print(f"smeltery export split: {e}", file=sys.stderr)
        return 2
    print(
        f"wrote {args.kind} split of {split['table']['rows']} rows to {args.out}: "
        + ", ".join(f"{k}={len(v)}" for k, v in split["partitions"].items())
    )
    return 0
