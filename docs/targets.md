# Target definition files

A **target definition** is a `.toml` file that describes a binding pocket and nothing else. It is what MCP's
`screen_candidates` and `estimate_cost` take as `target` (a bare `.pdb` or `.pqr` pocket file also works), and
`smeltery target check FILE` validates one from the command line. Reader: `smeltery.target_file`.

A screen is a target, plus ligands, plus a measurement. The ligands come from the caller (`ligand_set`), the
measurement from the target class ([mcp.md](mcp.md#target-class-defaults)); the target file holds the rest.

## Format (schema_version 1)

```toml
schema_version = 1          # required; 1 is the only version

[[pocket.site]]             # explicit point charges ...
q = 1.0
atom = 2                    # `distance` Angstrom beyond reference-molecule atom `atom`,
from_atom = 1               # along the from_atom -> atom direction (0-based indices)
distance = 3.0

# ... or a pocket file:      [pocket] file = "pocket.pdb"      (.pdb or .pqr, relative to this file)
# ... or a structure:        [structure] spec = "1abc", reference_ligand = "LIG", pocket_cutoff = 8.0
# optionally:                [docking] ...   [gates] posebusters = true ...
```

| Key or section | Meaning |
|---|---|
| `schema_version` | Required, the integer `1`. Anything else is refused: a reader never guesses a format. |
| `[pocket]` / `[[pocket.site]]` | The point-charge field: `file` (a `.pdb` charged by pdb2pqr, or a `.pqr`), or one or more sites. |
| `[structure]` | A receptor structure to prepare (a `.pdb` path, a PDB id, or with `provider = "afdb"` an AlphaFold accession); it supplies the field and the docking receptor. |
| `[docking]` | A prepared receptor (`.pdbqt`) and box to dock the reference molecule into. |
| `[gates]` | Which pose checks run before any calculation (`posebusters`, `forcefield`, ...). |

Each section is the campaign config's own section, with exactly its keys and rules (see the module docstring of
`smeltery.cli` and `smeltery methods`), checked by the same validator `smeltery run` uses. The file must describe the
pocket: `[pocket]` or `[structure]` is required.

## Validation

Everything is refused loudly, with the file's name and the reason; nothing is ignored:

- no `schema_version`, or a version other than `1`;
- any other top-level section (`[candidates]`, `[cut]`, a measurement section such as `[field_interaction]`): those
  describe the ligands or the measurement, which are chosen elsewhere;
- an unknown key in a section, a missing required key, a bad value (the campaign validator's own message);
- a `[pocket] file` or `[docking] receptor` that does not exist (relative paths resolve against the target file);
- a file that is not TOML.

Relative file paths are made absolute against the target file, so the screen can stage them from anywhere.

## Example

[`examples/targets/two_site_pocket.toml`](../examples/targets/two_site_pocket.toml): two explicit point charges.

```sh
smeltery target check examples/targets/two_site_pocket.toml
```

## Not (yet) on `smeltery run`

`smeltery run` reads a whole campaign config, in which these same sections sit beside `[candidates]` and the
measurement; it does not take a separate target file. Merging one in would have to stage the target's own input files
into the run directory alongside the config's, which is not done here: copy the sections into the config instead.
