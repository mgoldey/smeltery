# Scaffold-growth fixtures

Real DiffSBDD output, kept so the post-processing is tested against what the model actually produces.

- `parent.sdf`: a 41-heavy-atom ligand in its bound conformer (heavy atoms only).
- `scaffold.sdf`: 32 of those atoms, in the order the model was given them.
- `grown_raw.sdf`: 16 raw samples, unsanitized, in the model's own atom order.

Produced 2026-10-10 with DiffSBDD commit 5d0d38d, checkpoint `crossdocked_fullatom_cond.ckpt`
(SHA-256 07f86764bf569aafbc40a9c15fc02de8e2550437dd0f17f657eab3abe66c372c),
`--add_n_nodes 9 --n_samples 16 --center ligand --timesteps 50 --resamplings 20`, one GTX 1080,
RDKit 2022.09.5 in the model's environment.

Measured on these 16 under the repair in `smeltery.generate.sbdd_post`: 12 do not sanitize, 3 lose the
scaffold, 1 passes the default filters (index 12). That is one scaffold and one run, a regression
fixture, not a benchmark.
