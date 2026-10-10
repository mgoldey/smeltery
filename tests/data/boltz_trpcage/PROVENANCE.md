# Provenance: real Boltz-2 output (Trp-cage, 20 residues)

Produced 2026-10-10 by `boltz predict` (boltz 2.2.1 from PyPI, torch 2.14.1+cpu, Python 3.11) on an
Intel i7-6800K CPU (`--accelerator cpu`, machine load ~40): 24 min 21 s of inference (progress bar
`1/1 [24:21]`), 3233 s wall including the ~6 GB weight download.

Command: `boltz predict trpcage.yaml --accelerator cpu --output_format pdb` with `input.yaml` (single-sequence
mode, `msa: empty`, so no sequence left the machine; default recycling 3, sampling steps 200, 1 diffusion sample).
No `--seed` was given (the run is not seeded).

Files: `query_model_0.pdb` and `confidence_query_model_0.json` are the unmodified outputs
`trpcage_model_0.pdb` / `confidence_trpcage_model_0.json`, renamed only so the provider's fixed input stem
`query` matches. The npz files were not kept.

Licence: Boltz-2 code and weights are MIT (see docs/boltz.md); the output is a model prediction, not
experimental data. Citation: Passaro et al. 2025, doi:10.1101/2025.06.14.659707.

Not evidence of accuracy: no comparison with an experimental structure was made, and Trp-cage is a
well-known small protein that may be in the training data.

sha256: pdb 615941cd9f0dedf66f2087303f0dcd7671f2660db7d862331a2d7dec75deb754,
json 91fc36663926ecf3e92b3c716d891f8a88ca54d83111be66af92a385182a9321.
