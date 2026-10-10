# Boltz-2 structure provider

`smeltery.providers.BoltzProvider` predicts a protein (optionally protein-ligand) structure from a
sequence with Boltz-2 and returns a `StructureResult` of kind `PREDICTED` (issue #23).

## What was verified, and where (read 2026-10-10)

| Claim | Source | Status |
|---|---|---|
| Code is MIT | https://raw.githubusercontent.com/jwohlwend/boltz/main/LICENSE ("MIT License", copyright 2024 Wohlwend, Corso, Passaro) | VERIFIED |
| Weights are MIT, academic and commercial use | Boltz README: "All the code and weights are provided under MIT license, making them freely available for both academic and commercial uses" (https://github.com/jwohlwend/boltz); Hugging Face model metadata for `boltz-community/boltz-2` reports `license: mit` (https://huggingface.co/api/models/boltz-community/boltz-2) | VERIFIED (two sources; the weights repo has no separate licence file beyond that metadata) |
| Affinity module is covered | README statement above covers "the weights"; `boltz2_aff.ckpt` is in the same HF repo | INFERRED from the blanket statement |
| Citation | Passaro et al. 2025, bioRxiv, doi 10.1101/2025.06.14.659707 (Boltz-2); Wohlwend et al. 2024, doi 10.1101/2024.11.19.624167 (Boltz-1); ColabFold (Mirdita et al. 2022, Nature Methods) only when the MSA server is used. README "Cite" section | VERIFIED |
| CLI | `boltz predict <yaml> --out_dir --accelerator [gpu,cpu,tpu] --output_format [pdb,mmcif] --seed ...`; https://raw.githubusercontent.com/jwohlwend/boltz/main/docs/prediction.md and `boltz predict --help` of the installed 2.2.1 | VERIFIED |
| Outputs | prediction.md lists `predictions/<stem>/<stem>_model_N.{pdb,cif}`, `confidence_<stem>_model_N.json`, `plddt_/pae_/pde_*.npz`. MEASURED (boltz 2.2.1): they are under `<out_dir>/boltz_results_<stem>/predictions/<stem>/`; the docs omit `boltz_results_<stem>/`. The provider accepts both | VERIFIED by a live run |
| Confidence scales | JSON `confidence_score`, `ptm`, `iptm`, `complex_plddt`: range [0, 1] (prediction.md). PDB B-factor of a polymer residue = pLDDT x 100 (`boltz/data/write/pdb.py` of the installed 2.2.1) | VERIFIED |
| CPU supported | `--accelerator cpu` (prediction.md); README: CPU is "significantly slower" | VERIFIED |

Weights are fetched by `boltz predict` on first run into `~/.boltz` (or `--cache`/`BOLTZ_CACHE`):
`boltz2_conf.ckpt` 2,286,561,469 B, `boltz2_aff.ckpt` 2,062,139,170 B, `mols.tar` 1,855,662,080 B (Hugging Face
file sizes) plus `ccd.pkl`; about 6.2 GB before extraction. The default download host is
`model-gateway.boltz.bio`, with Hugging Face as fallback (`boltz/main.py`).

## Install: not as a smeltery extra

`pip install 'smeltery[boltz]'` cannot be solved: `boltz==2.2.1` pins `gemmi==0.6.5`, while ferric (a core
dependency) requires `gemmi>=0.7` (uv: "ferric>=0.1.0rc6 and smeltery[boltz] are incompatible"). A broken extra
would also break `uv lock` for every user, so none is declared. The provider only shells out to the `boltz`
executable, so install boltz in its own environment and point at it:

```
uv venv /opt/boltz-env --python 3.11
uv pip install --python /opt/boltz-env/bin/python boltz==2.2.1       # CUDA torch wheels
# or CPU-only torch: add --extra-index-url https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match
```
```python
BoltzProvider(executable="/opt/boltz-env/bin/boltz", accelerator="cpu").predict("NLYIQWLKDGGPSSGRPPPS")
```

On Python 3.11 with CPU torch, `boltz==2.2.1` installed 70 packages (not the 89 of a CUDA resolve) into a 1.6 GB venv.

## Live run (measured)

Trp-cage `NLYIQWLKDGGPSSGRPPPS` (20 residues), single-sequence, boltz 2.2.1, torch 2.14.1+cpu, Python 3.11,
Intel i7-6800K (12 threads, machine load ~40, `--accelerator cpu`): 24 min 21 s of inference; the weight download
(~6.2 GB) took ~21 min on top. Result: `complex_plddt` 0.950 (0-1 scale), mean CA B-factor 95.0 (0-100 scale),
`ptm` 0.406, `confidence_score` 0.841. The B-factor = pLDDT x 100 relation holds on this output.
The opt-in test (`SMELTERY_RUN_BOLTZ=1 SMELTERY_BOLTZ_EXE=... pytest -k live`) then ran the provider end to end
through the real CLI and passed in 220 s (less machine load; weights cached) (the layout discrepancy was found in the earlier raw CLI run).
These numbers say nothing about accuracy (no experimental comparison; Trp-cage may be in the training data;
single-sequence mode). GPU not tried: the two GTX 1080 (Pascal, sm_61) were not tested, and recent PyPI torch
may not support that architecture (UNVERIFIED either way). Real output is committed under
`tests/data/boltz_trpcage/` (see its PROVENANCE.md).

## Behaviour

* `StructureResult.plddt` is per-residue pLDDT on the 0-100 scale (from the PDB B-factors, CA atoms);
  `metadata["boltz_confidence"]` carries the model's own 0-1 numbers. PAE is not requested.
* License id `MIT`, `weights_source` the Hugging Face repo, `attribution` the Boltz-2 and Boltz-1 citations
  (plus ColabFold if `use_msa_server=True`).
* Default is single-sequence mode (`msa: empty`): no sequence leaves the machine, at lower accuracy than with
  an MSA (Boltz says so). `use_msa_server=True` sends the sequence to api.colabfold.com.
* Cannot-answer cases return `None` with `last_error`: invalid sequence, executable missing, non-zero exit
  (a GPU failure suggests `accelerator='cpu'`), timeout, missing outputs, malformed or out-of-range
  confidence, residue count different from the input.

## Opt-in live test

`SMELTERY_RUN_BOLTZ=1 pytest tests/test_boltz_provider.py -k live` (needs `boltz` on PATH and the weights).
