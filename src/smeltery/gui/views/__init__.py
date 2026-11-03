"""The viewers' drawing code: the only part of smeltery that imports marimo, anywidget, mols2grid, plotly or
molviewspec, and only inside functions, so `import smeltery.gui` (and `smeltery.gui.views`) works without the `gui`
extra.

Each module turns the data layer's tables (`smeltery.gui.results`, docs/gui-contract.md section 2) into one view:

* `forest`: paired ddE mean +/- z*SEM per analogue, tie groups as bands, the keep boundary only where the cut put one.
* `matrix`: the "resolved?" heatmap, the funnel's own predicate.
* `per_pose`: parent -> analogue paired lines, one per pose.
* `funnel`: candidates in and out per stage.
* `grid`: a mols2grid grid of the candidates with their ddE, group, survivor flag and decision.
* `pose`: Mol* showing `scenes.pose_scene(...)`.
* `builder`: the pipeline builder over `smeltery.gui.Pipeline`.
* `notebook`: the Marimo app that lays them out (`smeltery.gui.app.main` serves it).

The views draw what the cut decided and nothing more: no view orders the members of a tie group.
"""
