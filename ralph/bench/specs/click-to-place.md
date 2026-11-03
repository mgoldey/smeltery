# Click-to-place a pocket site

**Clicking two atoms adds one `[[pocket.site]]` that places charges on the atoms the run will use.**

## Why this is delicate
`[[pocket.site]]` stores atom indices into the parent's pose 0 (`cli._place_sites` reads
`parent.poses[0].coords_ang`), and that pose is only made during the run. Before the run, the user
clicks a *provisional* conformer. That is safe only if the provisional atom order equals the run's
atom order. If it ever differs, charges land on the wrong atoms with no error. That is the worst
silent failure Bench could have.

## Requirements

1. `smeltery.bench.provisional.provisional_pose(smiles: str, *, seed: int = 0xF00D) ->
   tuple[tuple[str, ...], numpy.ndarray]`: symbols and coordinates in
   `Chem.AddHs(Chem.MolFromSmiles(smiles))` order, embedded with ETKDGv3 and that seed. The UI
   labels it "provisional geometry"; it is never scored or written to a record.
2. `place_site(pipeline, atom: int, from_atom: int, *, q: float = 1.0, distance: float = 3.0) ->
   Pipeline` appends exactly one `{q, atom, from_atom, distance}` through
   `Pipeline.set("pocket", site=[...])`, and rejects `atom == from_atom` with `ValueError`.
3. The pipeline is the only state: no viewer-side selection object exists, and the TOML round-trips
   (`Pipeline.from_toml(p.to_toml())` keeps the sites).
4. **The atom-order anchor.** A test compares the provisional order with the order of the atoms in
   the run's pose files, read with `smeltery.gui.results.pose_sdf(run, candidate)` and parsed with
   RDKit (`removeHs=False`). The run-side order must come from those bytes. Deriving it from the
   SMILES is circular and does not count.
5. The anchor is proven live by mutation: permuting the atom order inside one pose SDF of the
   fixture makes it fail, and deleting `OUT/poses/` makes it error rather than pass.

## Data source for checks
`OUT/poses/<candidate>.sdf` as written by `make_run_dir`, through `pose_sdf`.
