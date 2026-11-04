# Structure tab

**Opening a structure shows what its preparation kept and dropped.**

## Requirements

1. `POST /api/structure {"source": str}` accepts a local `.pdb` path, a PDB id, or a UniProt
   accession, through `StructureSource`.
2. The response and page show the sha256 of each prepared file, `EXPERIMENTAL` or `PREDICTED`
   (with licence, attribution and mean pLDDT near the pocket when predicted), and every dropped
   HETATM by residue name.
3. A network source without `allow_network = true` is 400 with a message containing "network".
4. A missing file is 400 naming the path.

## Why dropped atoms are listed
184 of 324 molecules in this project's own study clashed into crystal waters. Silently dropped
atoms are how a result becomes wrong.

## In the app (Run → Structure)
5. A path field (and drag-and-drop of a `.pdb` file) opens a structure. The result shows each hash,
   the origin, and the dropped residues as a list, with the receptor drawn in the 3D viewer.
6. Errors appear inline beside the field with the API's message.
