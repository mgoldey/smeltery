"""Boltz-2 structure provider: sequence in, PREDICTED structure + per-residue pLDDT out.

The provider shells out to the `boltz predict` CLI through an injectable `runner`, exactly as
`AfdbProvider` injects `fetch`: tests supply a fake runner that writes fixture files, and the
real `boltz` package is only needed for the live (opt-in) path.

Install: NOT as a smeltery extra. boltz 2.2.1 pins gemmi==0.6.5 while ferric (a core dependency)
needs gemmi>=0.7, so `smeltery[boltz]` has no solution (measured with uv, 2026-10-10). Because the
provider only shells out to the `boltz` executable, install boltz in its own environment and pass
`executable=/path/to/that/env/bin/boltz`. See docs/boltz.md.

Verified facts (read 2026-10-10; URLs in docs/boltz.md):
  * code: MIT (github.com/jwohlwend/boltz LICENSE). Weights: the README states "All the code and
    weights are provided under MIT license" and the Hugging Face model card metadata for
    boltz-community/boltz-2 says `license: mit`. Both are recorded as license_id "MIT".
  * `boltz predict` writes `<out_dir>/boltz_results_<stem>/predictions/<stem>/<stem>_model_0.pdb`
    (with --output_format pdb;
    the docs draw it without `boltz_results_<stem>/`, which a live run showed to be wrong) and
    `confidence_<stem>_model_0.json`. In the PDB the B-factor column of a polymer residue is
    pLDDT x 100 (boltz/data/write/pdb.py), i.e. the AlphaFold scale 0-100. The JSON `complex_plddt`
    and `confidence_score` are on the 0-1 scale.
  * `--accelerator cpu` exists (docs/prediction.md); the default is gpu.

A provider that cannot answer returns None and says why in `last_error`; it never fabricates a
structure. Output whose residue count disagrees with the input, or whose confidence is missing or
out of range, is rejected rather than repaired.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

from .structure import StructureResult, plddt_from_pdb

BOLTZ_LICENSE_ID = "MIT"
BOLTZ_WEIGHTS_SOURCE = "https://huggingface.co/boltz-community/boltz-2 (boltz2_conf.ckpt, mols.tar)"
BOLTZ_ATTRIBUTION = (
    "Boltz-2 (code and weights: MIT License). Passaro et al. 2025, 'Boltz-2: Towards Accurate and "
    "Efficient Binding Affinity Prediction', bioRxiv, doi:10.1101/2025.06.14.659707; "
    "Wohlwend et al. 2024, 'Boltz-1: Democratizing Biomolecular Interaction Modeling', bioRxiv, "
    "doi:10.1101/2024.11.19.624167."
)
COLABFOLD_ATTRIBUTION = (
    " MSA from the ColabFold MMseqs2 server: Mirdita et al. 2022, 'ColabFold: making protein folding "
    "accessible to all', Nature Methods."
)
INSTALL_HINT = (
    "the boltz executable was not found. boltz cannot be a smeltery extra (its gemmi==0.6.5 pin conflicts "
    "with ferric's gemmi>=0.7): install it in a separate environment (`pip install boltz`) and pass "
    "executable='/path/to/that/env/bin/boltz'; see docs/boltz.md"
)
_SEQ_RE = re.compile(r"^[ACDEFGHIKLMNPQRSTVWY]+$")
_SMILES_RE = re.compile(r"^[^\s'\"]+$")


@dataclass(frozen=True)
class RunOutcome:
    """What a runner reports: the exit code and the captured text. The outputs are on disk."""

    returncode: int
    stdout: str = ""
    stderr: str = ""


#: runner(argv, timeout_s) -> RunOutcome. The default shells out; tests inject a fake that writes
#: output files. Raising FileNotFoundError means "the boltz executable is not installed".
Runner = Callable[[list[str], float | None], RunOutcome]


def subprocess_runner(argv: list[str], timeout: float | None) -> RunOutcome:
    if shutil.which(argv[0]) is None:
        raise FileNotFoundError(argv[0])
    p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)  # noqa: S603
    return RunOutcome(p.returncode, p.stdout, p.stderr)


def _installed_boltz_version() -> str | None:
    try:
        return metadata.version("boltz")
    except metadata.PackageNotFoundError:
        return None


def _unit(x, name: str) -> float:
    """A finite number in [0, 1], else ValueError (confidence we cannot trust is not carried)."""
    if isinstance(x, bool) or not isinstance(x, int | float) or not math.isfinite(x) or not 0.0 <= x <= 1.0:
        raise ValueError(f"{name}={x!r} is not a finite number in [0, 1]")
    return float(x)


class BoltzProvider:
    """Predict a protein (optionally protein-ligand) structure with Boltz-2.

    `predict(sequence)` takes a one-letter amino-acid sequence (the 20 standard residues). A ligand
    SMILES can be added with `predict(sequence, ligand_smiles=...)`; it is passed to Boltz as a second
    entity and recorded in the result metadata.

    Confidence: `StructureResult.plddt` is per-residue pLDDT on the 0-100 scale (Boltz writes
    pLDDT x 100 into the PDB B-factor). `metadata["boltz_confidence"]` carries the model's own JSON
    (0-1 scale: confidence_score, ptm, iptm, complex_plddt, ...). PAE is not requested.

    MSA: by default `msa: empty` (single-sequence mode; Boltz documents this as lower accuracy) so
    that no sequence leaves the machine. `use_msa_server=True` sends the sequence to the ColabFold
    MMseqs2 server (api.colabfold.com) and adds the ColabFold citation.
    """

    name = "boltz2"

    def __init__(
        self,
        runner: Runner = subprocess_runner,
        *,
        accelerator: str = "cpu",
        recycling_steps: int = 3,
        sampling_steps: int = 200,
        diffusion_samples: int = 1,
        use_msa_server: bool = False,
        seed: int | None = 0,
        cache_dir: str | None = None,
        timeout_s: float | None = None,
        executable: str = "boltz",
        boltz_version: str | None = None,
    ) -> None:
        if accelerator not in ("cpu", "gpu"):
            raise ValueError(f"accelerator must be 'cpu' or 'gpu', got {accelerator!r}")
        self._runner = runner
        self.accelerator = accelerator
        self.recycling_steps = recycling_steps
        self.sampling_steps = sampling_steps
        self.diffusion_samples = diffusion_samples
        self.use_msa_server = use_msa_server
        self.seed = seed
        self.cache_dir = cache_dir
        self.timeout_s = timeout_s
        self.executable = executable
        self._boltz_version = boltz_version
        self.last_error: str | None = None

    def settings(self) -> dict:
        """Everything that changes the answer, JSON-able and stable (for RunRecord inputs/digest)."""
        return {
            "provider": self.name,
            "model": "boltz-2",
            "license_id": BOLTZ_LICENSE_ID,
            "weights_source": BOLTZ_WEIGHTS_SOURCE,
            "accelerator": self.accelerator,
            "recycling_steps": self.recycling_steps,
            "sampling_steps": self.sampling_steps,
            "diffusion_samples": self.diffusion_samples,
            "use_msa_server": self.use_msa_server,
            "seed": self.seed,
        }

    def _yaml(self, sequence: str, ligand_smiles: str | None) -> str:
        lines = ["version: 1", "sequences:", "  - protein:", "      id: A", f"      sequence: {sequence}"]
        if not self.use_msa_server:
            lines.append("      msa: empty")
        if ligand_smiles:
            lines += ["  - ligand:", "      id: B", f"      smiles: '{ligand_smiles}'"]
        return "\n".join(lines) + "\n"

    def _argv(self, yaml_path: Path, out_dir: Path) -> list[str]:
        argv = [
            self.executable,
            "predict",
            str(yaml_path),
            "--out_dir",
            str(out_dir),
            "--accelerator",
            self.accelerator,
            "--output_format",
            "pdb",
            "--recycling_steps",
            str(self.recycling_steps),
            "--sampling_steps",
            str(self.sampling_steps),
            "--diffusion_samples",
            str(self.diffusion_samples),
        ]
        if self.seed is not None:
            argv += ["--seed", str(self.seed)]
        if self.use_msa_server:
            argv.append("--use_msa_server")
        if self.cache_dir:
            argv += ["--cache", self.cache_dir]
        return argv

    def predict(self, spec: str, ligand_smiles: str | None = None) -> StructureResult | None:
        self.last_error = None
        sequence = spec.strip().upper()
        if not _SEQ_RE.match(sequence):
            self.last_error = f"{spec!r} is not a sequence of the 20 standard amino acids (one-letter codes)"
            return None
        if ligand_smiles is not None and not _SMILES_RE.match(ligand_smiles):
            self.last_error = "ligand SMILES is empty or contains whitespace/quotes"
            return None
        with tempfile.TemporaryDirectory(prefix="smeltery-boltz-") as tmp:
            return self._run(sequence, ligand_smiles, Path(tmp))

    def _run(self, sequence: str, ligand_smiles: str | None, tmp: Path) -> StructureResult | None:
        stem = "query"
        yaml_path = tmp / f"{stem}.yaml"
        yaml_path.write_text(self._yaml(sequence, ligand_smiles))
        out_dir = tmp / "out"
        try:
            outcome = self._runner(self._argv(yaml_path, out_dir), self.timeout_s)
        except FileNotFoundError:
            self.last_error = INSTALL_HINT
            return None
        except subprocess.TimeoutExpired:
            self.last_error = f"boltz predict timed out after {self.timeout_s} s"
            return None
        except OSError as e:
            self.last_error = f"could not run boltz: {type(e).__name__}: {e}"
            return None
        if outcome.returncode != 0:
            tail = (outcome.stderr or outcome.stdout or "").strip()[-400:]
            hint = ""
            if self.accelerator == "gpu" and re.search(r"cuda|gpu|accelerator", tail, re.I):
                hint = " (accelerator='gpu' failed; no usable GPU? retry with accelerator='cpu')"
            self.last_error = f"boltz predict exited {outcome.returncode}{hint}: {tail}"
            return None
        # Measured (boltz 2.2.1 live run): outputs land in <out_dir>/boltz_results_<stem>/predictions/<stem>/,
        # not <out_dir>/predictions/<stem>/ as docs/prediction.md draws it. Accept either.
        candidates = (out_dir / f"boltz_results_{stem}" / "predictions" / stem, out_dir / "predictions" / stem)
        pred = next((d for d in candidates if d.is_dir()), candidates[0])
        pdb_path = pred / f"{stem}_model_0.pdb"
        conf_path = pred / f"confidence_{stem}_model_0.json"
        for p in (pdb_path, conf_path):
            if not p.is_file():
                self.last_error = f"boltz exited 0 but did not write {p.relative_to(tmp)}"
                return None
        try:
            conf = json.loads(conf_path.read_text())
            if not isinstance(conf, dict):
                raise ValueError("confidence json is not an object")
            for key in ("confidence_score", "complex_plddt"):
                if key not in conf:
                    raise ValueError(f"confidence json lacks {key!r}")
            confidence = {k: _unit(conf[k], k) for k in ("confidence_score", "complex_plddt")}
            confidence.update({k: _unit(conf[k], k) for k in ("ptm", "iptm") if k in conf})
            text = pdb_path.read_text()
            plddt = plddt_from_pdb(text)
        except (ValueError, OSError) as e:  # JSONDecodeError is a ValueError
            self.last_error = f"unusable boltz output: {type(e).__name__}: {e}"
            return None
        if len(plddt) != len(sequence):
            self.last_error = f"boltz model has {len(plddt)} residues with CA atoms; the input has {len(sequence)}"
            return None
        if any(not (0.0 <= v <= 100.0) for _, v in plddt):
            self.last_error = "per-residue pLDDT outside 0-100 in the model B-factors; not carried"
            return None
        attribution = BOLTZ_ATTRIBUTION + (COLABFOLD_ATTRIBUTION if self.use_msa_server else "")
        return StructureResult(
            text=text,
            kind="PREDICTED",
            source=f"boltz predict ({self.accelerator})",
            license_id=BOLTZ_LICENSE_ID,
            attribution=attribution,
            plddt=plddt,
            weights_source=BOLTZ_WEIGHTS_SOURCE,
            metadata={
                "model": "boltz-2",
                "boltz_version": self._boltz_version or _installed_boltz_version() or "UNKNOWN",
                "plddt_scale": "0-100 (PDB B-factor = pLDDT x 100)",
                "boltz_confidence": confidence,
                "boltz_confidence_scale": "0-1",
                "sequence": sequence,
                "ligand_smiles": ligand_smiles,
                "msa": "ColabFold MMseqs2 server" if self.use_msa_server else "none (single-sequence)",
                "settings": self.settings(),
            },
        )
