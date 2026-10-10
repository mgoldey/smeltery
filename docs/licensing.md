# Licensing of the tools and data smeltery builds on

This page records what each tool's OWN text says about use, read from the primary source on the date shown.
It states what the text says; it is not legal advice. `tests/test_licensing.py` parses the table below and
fails when a row is incomplete, when a registered provider has no row, or when a tool marked EXCLUDED has an
adapter in the codebase (issue #23).

## How a row is kept honest

Every row has a status, a date read, a URL and (when VERIFIED) a quoted sentence:

* **VERIFIED**: the primary text (the LICENSE file, the README licence section, the terms page, or the
  weights' terms) was fetched on the date shown and the operative sentence is quoted verbatim, so a reader can
  check it. Whitespace and Markdown emphasis were normalised in the quote; the words were not changed
  (including the upstream typo "commerical" in the Chai-1 README).
* **INFERRED**: reasoned from something weaker than the licence text (a Hugging Face metadata label, a
  README that is silent, or a licence text whose exact variant is not stated). The Notes column says from what.
* **UNVERIFIED**: no primary statement could be read. The row says what is missing.

To refresh a row: re-read the URL, update the quote if the wording changed, set the date to the day read,
and run `pytest tests/test_licensing.py`. A row is as old as its date; nothing re-checks the web.
Only a terms page that says something different should change a Decision.

## The exclusion rule

A tool is **EXCLUDED** (and gets no adapter in smeltery) when any row for it that a user needs in order to
run it, or any row for its outputs, carries one of these restrictions in the text read:

* **E1** use of the weights or code is limited to non-commercial use or to non-commercial organisations;
* **E2** the weights may not be redistributed or shared;
* **E3** the outputs are limited to non-commercial use or may not be used to train other models;
* **E4** use in screening or design workflows is prohibited.

A tool is **ELIGIBLE** when the text read grants commercial use of code and weights without E1 to E4.
**NOT-CLEARED** means no restriction was found but the weights' terms could not be read, so no adapter claim is
made. Data and libraries that smeltery consumes are marked **N-A** (the rule is about structure-prediction
tools).

Excluded by this rule, from the table below:

| Tool | Rule | Where |
|---|---|---|
| AlphaFold 3 | E1, E2, E3 | `af3-weights`, `af3-output` (the AF3 source code alone is Apache-2.0, `af3-code`; it is not usable without the weights) |
| RoseTTAFold (original, 2021) | E1, E2 | `rf1-weights` (code is MIT; the trained weights and data are Rosetta-DL, non-commercial) |
| HelixFold3 | E1, E3, E4 | `helixfold3-code`, `helixfold3-weights` |

Check against the issue's premise: the primary sources support all three exclusions. Two further points the
issue did not make. (1) AlphaFold 3's SOURCE CODE is Apache-2.0, not non-commercial; the restriction is on the
parameters and the output. (2) RoseTTAFold-All-Atom is NOT excluded: its LICENSE states that the BSD licence
"covers both the source code and model weights". RoseTTAFold2's weights have no stated terms in the files read
(NOT-CLEARED).

## The table (all rows read 2026-10-10)

| ID | Covers | Licence | What matters for smeltery | Decision | Status | Read | URL | Operative sentence |
|---|---|---|---|---|---|---|---|---|
| boltz1-code | Boltz-1 code | MIT | commercial use and redistribution allowed | ELIGIBLE | VERIFIED | 2026-10-10 | https://raw.githubusercontent.com/jwohlwend/boltz/main/LICENSE | "Permission is hereby granted, free of charge, to any person obtaining a copy" |
| boltz1-weights | Boltz-1 weights | MIT | README covers "the weights"; the Hugging Face card for boltz-community/boltz-1 also says `license: mit` | ELIGIBLE | VERIFIED | 2026-10-10 | https://github.com/jwohlwend/boltz | "All the code and weights are provided under MIT license, making them freely available for both academic and commercial uses" |
| boltz2-code | Boltz-2 code (same repository and LICENSE as Boltz-1) | MIT | commercial use and redistribution allowed | ELIGIBLE | VERIFIED | 2026-10-10 | https://raw.githubusercontent.com/jwohlwend/boltz/main/LICENSE | "Permission is hereby granted, free of charge, to any person obtaining a copy" |
| boltz2-weights | Boltz-2 weights incl. the affinity checkpoint | MIT | README statement is blanket ("the weights"); Hugging Face metadata for boltz-community/boltz-2 is `license: mit`; no separate weights licence file | ELIGIBLE | VERIFIED | 2026-10-10 | https://github.com/jwohlwend/boltz | "Our model and code are released under MIT License, and can be freely used for both academic and commercial purposes." |
| chai1-code | Chai-1 code (chai-lab) | Apache-2.0 | commercial use, modification and redistribution allowed; patent clause terminates on patent suit | ELIGIBLE | VERIFIED | 2026-10-10 | https://raw.githubusercontent.com/chaidiscovery/chai-lab/main/LICENSE | "each Contributor hereby grants to You a perpetual, worldwide, non-exclusive, no-charge, royalty-free, irrevocable copyright license" |
| chai1-weights | Chai-1 model weights | Apache-2.0 | README states the licence covers weights and commercial use; Hugging Face metadata for chaidiscovery/chai-1 is `license: apache-2.0`; weights are downloaded on first run | ELIGIBLE | VERIFIED | 2026-10-10 | https://github.com/chaidiscovery/chai-lab | "Chai-1 is released under an Apache 2.0 License (both code and model weights), which means it can be used for both academic and commerical purposes, including for drug discovery." |
| af3-code | AlphaFold 3 source code | Apache-2.0 | code only; useless without the parameters | ELIGIBLE (tool excluded via af3-weights) | VERIFIED | 2026-10-10 | https://github.com/google-deepmind/alphafold3 | "AlphaFold 3 source code is licensed under the Apache License, Version 2.0" |
| af3-weights | AlphaFold 3 model parameters | AlphaFold 3 Model Parameters Terms of Use (custom) | non-commercial organisations only; no publishing or sharing of the parameters; parameters only if received from Google through an application | EXCLUDED | VERIFIED | 2026-10-10 | https://github.com/google-deepmind/alphafold3/blob/main/WEIGHTS_TERMS_OF_USE.md | "only available for non-commercial use by, or on behalf of, non-commercial organizations" and "must not publish or share AlphaFold 3 model parameters" |
| af3-output | AlphaFold 3 output (predictions) | AlphaFold 3 Output Terms of Use (custom) | non-commercial only; must not be used to train structure-prediction models; may be published | EXCLUDED | VERIFIED | 2026-10-10 | https://github.com/google-deepmind/alphafold3/blob/main/OUTPUT_TERMS_OF_USE.md | "Output are made available free of charge, for non-commercial use only" |
| rf1-code | RoseTTAFold (original, 2021) code | MIT | code only | ELIGIBLE (tool excluded via rf1-weights) | VERIFIED | 2026-10-10 | https://github.com/RosettaCommons/RoseTTAFold | "While the code is licensed under the MIT License, the trained weights and data for RoseTTAFold are made available for non-commercial use only under the terms of the Rosetta-DL Software license." |
| rf1-weights | RoseTTAFold (original) trained weights and data | Rosetta-DL Non-Commercial License Agreement (custom) | internal non-profit research only; software must stay at the institution; commercial use needs a paid UW licence | EXCLUDED | VERIFIED | 2026-10-10 | https://files.ipd.uw.edu/pub/RoseTTAFold/Rosetta-DL_LICENSE.txt | "for your internal, non-profit, non-commercial research use" |
| rfaa-all | RoseTTAFold-All-Atom code AND weights | BSD (3-clause by its text: redistribution conditions, no-endorsement clause) | the LICENSE file states it covers the weights; commercial use not restricted in that text | ELIGIBLE | VERIFIED | 2026-10-10 | https://raw.githubusercontent.com/baker-laboratory/RoseTTAFold-All-Atom/main/LICENSE | "This copyright and license covers both the source code and model weights referenced for download in the README file." |
| rf2-code | RoseTTAFold2 code | MIT | code only | ELIGIBLE (tool NOT-CLEARED via rf2-weights) | VERIFIED | 2026-10-10 | https://raw.githubusercontent.com/uw-ipd/RoseTTAFold2/main/LICENSE | "Copyright (c) 2023 Institute for Protein Design" |
| rf2-weights | RoseTTAFold2 weights (RF2_jan24.tgz) | none stated | the LICENSE file and README read say nothing about the weights; not shown to be restricted, not shown to be free | NOT-CLEARED | UNVERIFIED | 2026-10-10 | https://github.com/uw-ipd/RoseTTAFold2 | (no weights terms found in the LICENSE file or README) |
| helixfold3-code | HelixFold3 code and derivatives | custom "Terms of Use Agreement" (Attribution-NonCommercial-ShareAlike share-alike clause) | non-commercial individuals and organisations only; code and outputs not for commercial entities; not to be incorporated into workflows for screening or design of biomolecules | EXCLUDED | VERIFIED | 2026-10-10 | https://github.com/PaddlePaddle/PaddleHelix/blob/dev/apps/protein_folding/helixfold3/LICENSE | "The code and its outputs shall not be used by or for commercial entities" and "workflows for the screening or design of biomolecules" |
| helixfold3-weights | HelixFold3 model parameters | same terms as the code | README groups code and parameters under the one LICENSE | EXCLUDED | VERIFIED | 2026-10-10 | https://github.com/PaddlePaddle/PaddleHelix/tree/dev/apps/protein_folding/helixfold3 | "for non-commercial use by individuals or non-commercial organizations only." |
| openfold3-code | OpenFold3 code | Apache-2.0 | commercial use allowed | ELIGIBLE | VERIFIED | 2026-10-10 | https://github.com/aqlaboratory/openfold-3 | "our repository is freely available for academic and commercial use under the Apache 2.0 license." |
| openfold3-weights | OpenFold3-preview weights (Hugging Face OpenFold/OpenFold3, gated) | Apache-2.0 (label) | the Hugging Face API reports `license: apache-2.0` and gating `auto` (accept a contact-sharing form); the raw model card is behind the gate and was not fetched directly | ELIGIBLE | INFERRED | 2026-10-10 | https://huggingface.co/OpenFold/OpenFold3 | "The OpenFold3-preview model is released under Apache 2.0 license." (from a page fetch summary of the card; the repository sentence above is only about the repository) |
| esmfold-code | ESM / ESMFold code (repository archived 2024-08-01) | MIT | code only | ELIGIBLE | VERIFIED | 2026-10-10 | https://github.com/facebookresearch/esm | "This source code is licensed under the MIT license found in the LICENSE file in the root directory of this source tree." |
| esmfold-weights | ESMFold v1 weights | MIT (label) | the repository README states no weights licence (its licence section covers source code and the ESM Atlas data); the Hugging Face card for facebook/esmfold_v1 carries `license: mit` | ELIGIBLE | INFERRED | 2026-10-10 | https://huggingface.co/facebook/esmfold_v1 | (metadata field only: `license: mit`; no terms text) |
| af2-code | AlphaFold 2 code | Apache-2.0 | commercial use allowed | ELIGIBLE | VERIFIED | 2026-10-10 | https://github.com/google-deepmind/alphafold | "While the AlphaFold code is licensed under the Apache 2.0 License, the AlphaFold parameters and CASP15 prediction data are made available under the terms of the CC BY 4.0 license." |
| af2-weights | AlphaFold 2 parameters | CC-BY-4.0 | attribution required; commercial use allowed by CC-BY; AF2 output carries a "theoretical modeling only" disclaimer | ELIGIBLE | VERIFIED | 2026-10-10 | https://github.com/google-deepmind/alphafold | "The AlphaFold parameters are made available under the terms of the Creative Commons Attribution 4.0 International (CC BY 4.0) license." |
| afdb-data | AlphaFold DB predictions (data, not a model) | CC-BY-4.0 | attribution required; academic and commercial use; smeltery's AfdbProvider records `CC-BY-4.0` (agrees) | N-A | VERIFIED | 2026-10-10 | https://alphafold.ebi.ac.uk/download | "Data is available for academic and commercial use, under a CC-BY-4.0 licence." |
| pdb-data | RCSB PDB archive data | CC0-1.0 | public-domain dedication; smeltery's PdbProvider records `CC0-1.0` (agrees); data from external resources integrated into RCSB APIs follow their own terms | N-A | VERIFIED | 2026-10-10 | https://www.rcsb.org/pages/policies | "data files contained in the PDB archive are available under the CC0 1.0 Universal (CC0 1.0) Public Domain Dedication" |
| plb-data | Protein-Ligand Benchmark data (committed derived manifest) | CC-BY-4.0 | attribution REQUIRED; redistribution of the derived manifest allowed with attribution and a changes statement | N-A | VERIFIED | 2026-10-10 | https://github.com/openforcefield/protein-ligand-benchmark/blob/fd88824f9114244f95a14b485e6d6c96c1de716d/LICENSE_DATA | "Attribution 4.0 International" (file header; copyright line "Copyright (c) 2020, Open Forcefield Group") |
| rdkit-code | RDKit (used by property:rdkit-alerts) | BSD-3-Clause | redistribution with notices | N-A | VERIFIED | 2026-10-10 | https://raw.githubusercontent.com/rdkit/rdkit/master/license.txt | "Redistribution and use in source and binary forms, with or without modification, are permitted provided that the following conditions are met:" |
| vina-code | AutoDock Vina (used by docking:vina and scoring:vina-score) | Apache-2.0 | commercial use allowed | N-A | VERIFIED | 2026-10-10 | https://raw.githubusercontent.com/ccsb-scripps/AutoDock-Vina/develop/LICENSE | "each Contributor hereby grants to You a perpetual, worldwide, non-exclusive, no-charge, royalty-free, irrevocable copyright license" |
| meeko-code | Meeko (used by docking:vina) | LGPL-2.1 (-only versus -or-later NOT determined) | the LICENSE file is the LGPL version 2.1 text; its "any later version" wording appears only in the generic how-to-apply appendix, so which SPDX variant applies is not established; Meeko is an optional extra, not vendored | N-A | INFERRED | 2026-10-10 | https://raw.githubusercontent.com/forlilab/Meeko/develop/LICENSE | "GNU LESSER GENERAL PUBLIC LICENSE Version 2.1, February 1999" |

Notes on the table. The Boltz-1/Boltz-2 rows were re-read in this change, not copied from docs/boltz.md. The
Hugging Face labels were read from the Hugging Face API metadata (`/api/models/<id>`), which is a label, not terms
text. Quotes were also checked by string match against the fetched files on the date shown. The
`af2-weights` quote is the README's own Model Parameters License paragraph.

## Registered providers and their rows

`tests/test_licensing.py` requires that every provider registered in the `smeltery.providers` entry-point group
appears here, so a new provider cannot be added without naming the licence rows that cover it. Each row ID must
exist in the table above.

| Entry | Rows |
|---|---|
| structure:pdb | pdb-data |
| structure:afdb | afdb-data |
| structure:boltz2 | boltz2-code, boltz2-weights |
| property:rdkit-alerts | rdkit-code |
| scoring:vina-score | vina-code |
| docking:vina | vina-code, meeko-code |

## Second provider candidate: Chai-1 (listed, not implemented)

Chai-1 is the second structure provider on the list: `chai1-code` and `chai1-weights` are both Apache-2.0 and
VERIFIED above, so it passes the exclusion rule. No adapter is written and none is registered. When one is added
it must add its `structure:chai1` entry to the registered-provider table above in the same change (the test
enforces this), and record the weights source and licence in the run record the way `BoltzProvider` does.
Other ELIGIBLE candidates from this table: OpenFold3 (weights INFERRED), RoseTTAFold-All-Atom, ESMFold
(weights INFERRED).

## What smeltery itself ships

* **Code**: `MIT OR Apache-2.0` (`pyproject.toml`; `LICENSE-MIT`, `LICENSE-APACHE`).
* **PLB manifest** `benchmarks/plb/manifest.json`: derived from the Open Force Field protein-ligand-benchmark at
  commit `fd88824f9114244f95a14b485e6d6c96c1de716d`; data licence CC-BY-4.0, attribution required and carried in
  the manifest (`licence.attribution`, which the loader refuses to run without); see `benchmarks/plb/README.md`.
  The PLB protein and ligand files are fetched, not committed.
* **Boltz-2 fixture** `tests/data/boltz_trpcage/`: a real Boltz-2 prediction for Trp-cage; the model code and
  weights are MIT (rows above); a prediction is not experimental data; see its `PROVENANCE.md`.
* **Test structures**: `tests/data/pocket_fixture/7LCJ_pocket.pdb` carries the `HEADER ... 7LCJ` record of a PDB entry
  (RCSB data, CC0-1.0, `pdb-data`); `tests/data/afdb_tiny.pdb` is a 16-line stand-in with AFDB-style per-residue
  confidence values and no AlphaFold DB header, so it is not an AFDB download (inferred from the file: no header,
  and the tests give it a made-up `example.test` URL).
* **Experiments** under `experiments/` (e.g. the 7LCJ structure) are frozen records;
  their data follow the sources' own terms (the PDB entry is CC0-1.0) and are outside this table.
