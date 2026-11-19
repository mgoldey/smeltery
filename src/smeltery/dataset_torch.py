"""A map-style dataset over an exported table (docs/dataset-export.md), for PyTorch's `DataLoader`.

`ExportDataset(table, features=[...], target="score")` reads the Parquet or Arrow file `smeltery export` wrote and
nothing else: no run record, no tier, no re-run. A sample is `(x, y)`: `x` the caller's feature columns in the order
given, `y` the target column, both as float32 tensors (1-D and 0-D), then the caller's `transform(sample)` if any.

* **Features and target are numeric columns the caller names.** A name that is not a column, or a column that is not
  numeric, is refused when the dataset is built. A null feature becomes NaN (it is a value the table does not have; the
  caller decides what to do, e.g. in `transform`). A row with a null TARGET is dropped, because a label cannot be
  imputed, and the count is `n_null_target`.
* **Filters.** `tiers` and `decisions` keep the rows whose `tier` / `decision` is listed. A row filtered out is not
  silently renumbered into another partition: filtering happens after the split picks the partition.
* **Splits.** `split=<file>, partition="train"` loads the versioned split artifact (dataset_splits) and keeps that
  partition's rows; the split must have been made on this very table file, else `SplitError`. Each of `split` and
  `partition` without the other is refused: an unsplit dataset is built without either.
* **A dataset directory** (`smeltery export dataset`, smeltery.dataset_record) is accepted as `table`: it is checked
  against its record first (every file's sha256 and size, the content digest; `dataset_sha256=` additionally pins the
  digest the caller expects), its table is read, and `split` may be a split NAME the record lists (or its path in the
  directory). `lineage_entries()` then gives the `data[]` entries the training run's core record lists (the dataset
  by content digest, the split file by its digest), which is what links the run to the dataset by digest.
* **torch is optional and imported when the dataset is built** with `tensors=True`. `tensors=False` yields plain
  Python floats, which needs no torch (and which `DataLoader` also accepts, since a map-style dataset is only
  `__len__` + `__getitem__`). The class deliberately does not subclass `torch.utils.data.Dataset`: that would make
  importing this module import torch.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from pathlib import Path

from .dataset_splits import SplitError, load_split, partition_indices, read_table

DECISIONS = ("pass", "drop", "reference", "undecided")
_NUMERIC_KINDS = ("int", "uint", "float", "double", "halffloat", "decimal", "bool")


def _torch():
    try:
        import torch
    except ImportError as e:
        raise ImportError(
            "ExportDataset(tensors=True) needs PyTorch, the optional `torch` extra: pip install 'smeltery[torch]' "
            "(or pass tensors=False for plain floats)"
        ) from e
    return torch


def _is_numeric(arrow_type) -> bool:
    return any(str(arrow_type).startswith(k) for k in _NUMERIC_KINDS)


def _floats(table, name: str) -> list[float]:
    return [math.nan if v is None else float(v) for v in table.column(name).to_pylist()]


class ExportDataset:
    def __init__(
        self,
        table: str | Path,
        *,
        features: Sequence[str],
        target: str,
        split: str | Path | None = None,
        partition: str | None = None,
        tiers: Sequence[str] | None = None,
        decisions: Sequence[str] | None = None,
        transform: Callable | None = None,
        tensors: bool = True,
        dataset_sha256: str | None = None,
    ):
        if not features:
            raise ValueError("features must name at least one column")
        if (split is None) != (partition is None):
            raise ValueError("pass both `split` and `partition`, or neither")
        bad = [d for d in decisions or () if d not in DECISIONS]
        if bad:
            raise ValueError(f"unknown decision(s) {bad}; known: {', '.join(DECISIONS)}")
        self._torch = _torch() if tensors else None
        self.dataset = None  # the opened dataset directory (dataset_record.OpenedDataset), when `table` is one
        self.split_name: str | None = None
        from . import dataset_record

        if dataset_record.is_dataset_dir(table):
            self.dataset = dataset_record.open_dataset(table, expect_sha256=dataset_sha256)
            table = self.dataset.table
            if split is not None:
                self.split_name, split = self.dataset.split(split)
        elif dataset_sha256 is not None:
            raise ValueError("dataset_sha256 applies to a dataset directory; this table is a bare file")
        tbl = read_table(table)
        for c in (*features, target):
            if c not in tbl.column_names:
                raise ValueError(f"table has no column {c!r}")
            if not _is_numeric(tbl.schema.field(c).type):
                raise ValueError(f"column {c!r} is {tbl.schema.field(c).type}, not numeric")
        keep = set(range(tbl.num_rows))
        if split is not None:
            parts = partition_indices(load_split(split, table), table)
            if partition not in parts:
                raise SplitError(f"split has no partition {partition!r}; it has {sorted(parts)}")
            keep &= set(parts[partition])
        if tiers is not None:
            keep &= {i for i, t in enumerate(tbl.column("tier").to_pylist()) if t in set(tiers)}
        if decisions is not None:
            keep &= {i for i, d in enumerate(tbl.column("decision").to_pylist()) if d in set(decisions)}
        y_all = _floats(tbl, target)
        self.n_null_target = sum(1 for i in keep if math.isnan(y_all[i]))
        rows = sorted(i for i in keep if not math.isnan(y_all[i]))
        cols = [_floats(tbl, c) for c in features]
        ids = list(zip(*(tbl.column(c).to_pylist() for c in ("run_id", "ligand", "target", "tier")), strict=True))
        self.features, self.target = list(features), target
        self._x = [[col[i] for col in cols] for i in rows]
        self._y = [y_all[i] for i in rows]
        self._ids = [ids[i] for i in rows]
        self._transform = transform

    def __len__(self) -> int:
        return len(self._y)

    def __getitem__(self, i: int):
        if self._torch is not None:
            t = self._torch
            sample = (t.tensor(self._x[i], dtype=t.float32), t.tensor(self._y[i], dtype=t.float32))
        else:
            sample = (list(self._x[i]), self._y[i])
        return self._transform(sample) if self._transform else sample

    def lineage_entries(self, role: str = "train") -> list[dict]:
        """`data[]` entries for the core record of a run trained on this dataset: the dataset by its content digest
        and, when built from a split the record lists, the split file by its digest. Only for a dataset directory: a
        bare table file has no record a resolver could link to."""
        if self.dataset is None:
            raise ValueError("lineage entries need a dataset directory (smeltery export dataset), not a bare table")
        return self.dataset.lineage_entries(role=role, split=self.split_name)

    def identity(self, i: int) -> tuple[str, str, str, str]:
        """(run_id, ligand, target, tier) of sample `i`, to trace a prediction back to its row."""
        return self._ids[i]
