"""`ScaffoldGrowth`: DiffSBDD scaffold inpainting behind the `CandidateGenerator` protocol.

DiffSBDD runs in its own Python environment (it needs Python 3.10 and torch 2.0.1; smeltery needs RDKit >= 2024.3 on
Python 3.11+), so this class talks to it through `_sbdd_runner.py` in a subprocess with JSON and SDF files. See
docs/generators.md for setting the environment up and docs/superpowers/specs/2026-10-10-sbdd-scaffold-growth-design.md
for the measured yield: on the scaffold it was developed against, about 1 raw sample in 16 survived, so
`target_accepted` and the rejection counts matter more than the model's headline speed.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from os import PathLike
from pathlib import Path

from rdkit import Chem

from ..model import Candidate
from .base import GenerationResult, GeneratorError, Rejection
from .sbdd_post import POSE_SOURCES, FilterConfig, build_scaffold, process_samples

RUNNER = Path(__file__).with_name("_sbdd_runner.py")
STDERR_TAIL_CHARS = 2000


def _sha256_of(path: str | PathLike) -> str:
    """SHA-256 of a file's bytes, streamed; hashing never loads the (pickled) checkpoint."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tail(text: str | bytes | None) -> str:
    if text is None:
        return ""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    return text[-STDERR_TAIL_CHARS:]


def _write_sdf(path: Path, mol: Chem.Mol) -> None:
    writer = Chem.SDWriter(str(path))
    writer.write(mol)
    writer.close()


def _read_records(path: Path) -> list[Chem.Mol | None]:
    """Every record of an SDF, unsanitized, with None for one RDKit cannot parse.

    A run that produced no molecules leaves an empty file, which `SDMolSupplier` refuses to open (OSError), so an
    empty file is read as no records.
    """
    if path.stat().st_size == 0:
        return []
    return list(Chem.SDMolSupplier(str(path), sanitize=False, removeHs=False))


@dataclass
class ScaffoldGrowth:
    python: str  # the interpreter that has torch and DiffSBDD's dependencies
    diffsbdd_dir: str  # a DiffSBDD checkout
    checkpoint: str  # a conditional checkpoint, e.g. crossdocked_fullatom_cond.ckpt
    n_samples: int = 16  # samples per batch
    add_n_nodes: int | None = None  # atoms to add to the scaffold; None lets the model choose
    timesteps: int = 50  # inpaint.py's own defaults; the training value (500) with 1 resampling did worse
    resamplings: int = 20
    center: str = "ligand"
    device: str | None = None  # CUDA_VISIBLE_DEVICES for the runner; None leaves the environment as it is
    pose_source: str = "embed"  # "embed": SMILES only; "model": also carry the model's geometry as pose 0
    filters: FilterConfig = field(default_factory=FilterConfig)
    target_accepted: int | None = None  # keep sampling batches until this many are accepted (or max_batches)
    max_batches: int = 1  # only used with target_accepted
    name_prefix: str = "sbdd"
    keep_workdir: str | None = None  # keep request, report and SDF files here; None uses a temp dir that is removed
    timeout_s: float | None = None  # per batch
    runner: str | None = None  # replace the packaged runner (tests)
    name: str = "sbdd_scaffold_growth"

    def settings(self) -> dict:
        return {
            "generator": "diffsbdd-inpaint",
            "python": self.python,
            "diffsbdd_dir": self.diffsbdd_dir,
            "checkpoint": self.checkpoint,
            "n_samples": self.n_samples,
            "add_n_nodes": self.add_n_nodes,
            "timesteps": self.timesteps,
            "resamplings": self.resamplings,
            "center": self.center,
            "device": self.device,
            "pose_source": self.pose_source,
            "target_accepted": self.target_accepted,
            "max_batches": self.max_batches,
            "name_prefix": self.name_prefix,
            "filters": asdict(self.filters),
        }

    def generate(
        self, parent: Candidate, scaffold: Sequence[int], pocket: str | PathLike, *, seed: int
    ) -> GenerationResult:
        self._validate(pocket)
        if not parent.poses:
            raise ValueError(
                f"{parent.name}: the parent needs its bound pose as poses[0] to fix the scaffold's coordinates"
            )
        spec = build_scaffold(parent.smiles, scaffold, parent.poses[0])
        own_dir = self.keep_workdir is None
        workdir = Path(tempfile.mkdtemp(prefix="smeltery-sbdd-")) if own_dir else Path(self.keep_workdir).resolve()
        workdir.mkdir(parents=True, exist_ok=True)
        try:
            return self._generate(spec, str(Path(pocket).resolve()), seed, workdir)
        finally:
            if own_dir:
                shutil.rmtree(workdir, ignore_errors=True)

    def _validate(self, pocket: str | PathLike) -> None:
        if self.pose_source not in POSE_SOURCES:
            raise ValueError(f"pose_source must be one of {POSE_SOURCES}, not {self.pose_source!r}")
        if self.n_samples < 1 or self.max_batches < 1:
            raise ValueError("n_samples and max_batches must be at least 1")
        for label, path in (("pocket", pocket), ("checkpoint", self.checkpoint), ("diffsbdd_dir", self.diffsbdd_dir)):
            if not Path(path).exists():
                raise ValueError(f"{label} not found: {path}")
        if self.runner is None and not RUNNER.is_file():
            raise ValueError(f"the packaged runner is missing: {RUNNER}")
        if shutil.which(self.python) is None:
            raise ValueError(f"python not found or not executable: {self.python}")

    def _generate(self, spec, pocket: str, seed: int, workdir: Path) -> GenerationResult:
        started = time.time()
        checkpoint_sha256 = _sha256_of(self.checkpoint)
        reference, fixed = workdir / "parent.sdf", workdir / "scaffold.sdf"
        _write_sdf(reference, spec.parent)
        _write_sdf(fixed, spec.scaffold)
        n_batches = self.max_batches if self.target_accepted is not None else 1
        candidates: list[Candidate] = []
        rejected: list[Rejection] = []
        seen: set[str] = set()
        runs: list[dict] = []
        n_raw = 0
        for batch in range(n_batches):
            report = self._run_batch(workdir, batch, seed + batch, pocket, reference, fixed, checkpoint_sha256)
            raws = _read_records(workdir / f"grown_{batch}.sdf")
            if len(raws) != report["n_raw"]:
                raise GeneratorError(f"the runner reported {report['n_raw']} samples but the SDF holds {len(raws)}")
            got, lost = process_samples(
                raws,
                spec,
                self.filters,
                pose_source=self.pose_source,
                name_prefix=self.name_prefix,
                start_index=n_raw,
                first_number=len(candidates),
                seen=seen,
            )
            n_raw += len(raws)
            candidates += got
            rejected += lost
            runs.append(report)
            if self.target_accepted is not None and len(candidates) >= self.target_accepted:
                break
        result = GenerationResult(candidates, rejected, {})
        result.provenance = {
            "generator": self.name,
            "seed": seed,
            "checkpoint_sha256": checkpoint_sha256,
            "n_batches": len(runs),
            "n_raw": n_raw,
            "n_accepted": len(candidates),
            "rejection_counts": result.rejection_counts(),
            "wall_time_s": round(time.time() - started, 3),
            "runs": runs,
        }
        return result

    def _run_batch(
        self, workdir: Path, batch: int, seed: int, pocket: str, reference: Path, fixed: Path, checkpoint_sha256: str
    ) -> dict:
        out_sdf = workdir / f"grown_{batch}.sdf"
        out_report = workdir / f"report_{batch}.json"
        request = workdir / f"request_{batch}.json"
        request.write_text(
            json.dumps(
                {
                    "diffsbdd_dir": str(Path(self.diffsbdd_dir).resolve()),
                    "checkpoint": str(Path(self.checkpoint).resolve()),
                    "pdb": pocket,
                    "ref_ligand": str(reference),
                    "fix_atoms": str(fixed),
                    "out_sdf": str(out_sdf),
                    "out_report": str(out_report),
                    "n_samples": self.n_samples,
                    "add_n_nodes": self.add_n_nodes,
                    "timesteps": self.timesteps,
                    "resamplings": self.resamplings,
                    "center": self.center,
                    "seed": seed,
                },
                indent=2,
            )
        )
        runner = self.runner or str(RUNNER)
        if self.runner is not None and Path(self.runner).is_file():
            runner = str(Path(self.runner).resolve())
        env = dict(os.environ)
        if self.device is not None:
            env["CUDA_VISIBLE_DEVICES"] = str(self.device)
        try:
            proc = subprocess.run(
                [self.python, runner, str(request)],
                capture_output=True,
                text=True,
                errors="replace",
                env=env,
                cwd=str(Path(self.diffsbdd_dir).resolve()),
                timeout=self.timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            raise GeneratorError(f"the runner did not finish within {self.timeout_s} s: {_tail(exc.stderr)}") from exc
        except OSError as exc:
            raise GeneratorError(f"could not start {self.python!r}: {exc}") from exc
        if proc.returncode != 0:
            raise GeneratorError(f"the runner exited with status {proc.returncode}: {proc.stderr[-STDERR_TAIL_CHARS:]}")
        try:
            report = json.loads(out_report.read_text())
            if not isinstance(report, dict) or (report.get("ok") and "n_raw" not in report):
                raise ValueError("report is not an object with n_raw")
        except (OSError, ValueError) as exc:
            raise GeneratorError("the runner left no readable report") from exc
        if not report.get("ok"):
            raise GeneratorError(f"the runner reported a failure: {str(report.get('error'))[-STDERR_TAIL_CHARS:]}")
        if report.get("checkpoint_sha256") != checkpoint_sha256:
            raise GeneratorError(
                f"the runner hashed a different checkpoint ({report.get('checkpoint_sha256')}) "
                f"than {self.checkpoint} ({checkpoint_sha256})"
            )
        if not out_sdf.exists():
            raise GeneratorError("the runner reported success but wrote no SDF")
        return report
