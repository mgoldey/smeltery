# Report

**A bench chemist reads a served report and comments on it; a frozen copy archives it.**

## Requirements

1. `GET /report/{idx}` is the served report. The verdict appears before the candidate table.
2. `POST /api/runs/{idx}/comments {"anchor","body"}` stores a comment through `CommentStore`, with
   the author from `Identity`. `anchor` is `run`, `candidate:<name>` or `pose:<name>:<i>`.
3. `POST /api/runs/{idx}/snapshot` writes the frozen report beside the run with the existing
   `smeltery.gui.report.write_report`, unmodified. Every number, plot and table in it is inline and needs no
   network. Its only `http(s)://` references are the pinned, integrity-hashed Mol* and RDKit.js files the GUI
   contract (section 4) designs it to load, and it says so when they cannot load. (An earlier version of this
   spec asked for zero references; meeting that by deleting the URLs broke the report's 2D/3D views.)
4. Both reports render from `smeltery.gui.results` tables, so every ΔΔE mean in the served report
   appears in the frozen one.
5. No route on the report surface changes the pipeline, the method, the floor, or starts a task.

## In the app (Report area, `/report/:idx`)
6. Read-only layout, in order: the verdict, the forest plot, candidates with 2D structures, and a
   pose viewer.
7. Each candidate, each pose and the run as a whole has a comment thread; adding a comment shows it
   immediately with its author.
8. The page has no control that edits the pipeline, the method or the floor, or that launches work.
9. "Archive snapshot" writes the frozen report and shows its path and sha256.
