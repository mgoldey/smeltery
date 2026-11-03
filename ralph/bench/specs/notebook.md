# Notebook

**Notebook entries are append-only and reference artefacts by hash.**

## Requirements

1. `POST /api/campaigns/{id}/entries {"title","body","runs":[...],"supersedes"?}` creates an entry,
   with the author from `Identity` and time from `Clock`.
2. Artefacts are recorded as sha256 hashes through `ArtifactStore`, never as paths.
3. There is no edit route: `PUT` and `PATCH` on an entry are 404 or 405, and `ProjectStore` has no
   method that mutates an existing entry.
4. A correction is a new entry with `supersedes` set. The superseded entry stays readable.
