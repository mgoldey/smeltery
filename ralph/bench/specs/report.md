# Report

**A bench chemist reads a served report and comments on it; a frozen copy archives it.**

## Requirements

1. `GET /report/{idx}` is the served report. The verdict appears before the candidate table.
2. `POST /api/runs/{idx}/comments {"anchor","body"}` stores a comment through `CommentStore`, with
   the author from `Identity`. `anchor` is `run`, `candidate:<name>` or `pose:<name>:<i>`.
3. `POST /api/runs/{idx}/snapshot {"path"}` writes the frozen report with the existing
   `smeltery.gui.report.write_report`. The file has zero `http(s)://` references.
4. Both reports render from `smeltery.gui.results` tables, so every ΔΔE mean in the served report
   appears in the frozen one.
5. No route on the report surface changes the pipeline, the method, the floor, or starts a task.
