# Results view

**The ΔΔE result and its resolution verdict are shown honestly.**

## Requirements

1. `GET /api/runs/{idx}/ddE` returns `gui.results.ddE_table(run)` rows plus `floor`, `floor_source`,
   `z`, `keep`, and the verdict. There is no `score`, `overall` or `composite` column.
2. `GET /api/runs/{idx}/resolved` returns `gui.results.resolved_matrix(run)` exactly, so it cannot
   disagree with `funnel.resolved`.
3. `GET /api/runs/{idx}/per_pose` returns `per_pose_table(run, "dE_int")`.
4. The page draws the forest plot (mean ± z·SEM), tie groups as alternating bands **and** labels,
   a ±floor band at group boundaries, and per-pose paired lines with the unpaired SEM beside them.
5. UNRANKED renders as "does not resolve" with the floor, as large as a winner would be, and the
   word "winner" does not appear. INCOMPLETE renders as itself.
6. No rankograms, SUCRA or Monte Carlo ranks: the cut combines the floor by max, not in quadrature.

## Honesty anchors
Each of these is a test, proven live by mutation (break the code, confirm the test fails, revert):
UNRANKED rendered as a winner; the floor band or its source dropped; INCOMPLETE rendered blank;
the served resolved matrix differing from `resolved_matrix`; a composite score column appearing.

## In the app (Run → Results)
7. The forest plot, resolved matrix and per-pose plot render from
   `/api/runs/{idx}/figures/{forest,matrix,per_pose}`. Python builds the figures and the browser
   draws them unchanged.
8. The verdict banner is the largest text on the page. UNRANKED reads "does not resolve" with the
   floor value; INCOMPLETE lists what the record is missing.
9. Tie groups carry text labels as well as bands.
