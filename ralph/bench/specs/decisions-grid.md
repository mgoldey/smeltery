# Decisions grid

**Keep/reject/hold decisions are recorded through `DecisionSet` alone.**

## Requirements

1. `POST /api/runs/{idx}/decisions {"candidate","status","reason","note"}` calls
   `DecisionSet.set` and `dump`, writing `OUT/decisions-<input_digest[:12]>.json`.
2. Every refusal comes from `DecisionSet.validate` / `Decision.problems`; the surface adds no rule.
   A `within_tie_choice` or `other` without a note, an unknown reason, and an unknown status are
   all 400 with the core's message.
3. The `user` comes from `Identity.current()`. A `user` field in the request is ignored.
4. Statuses are `STATUSES`; reasons are `REASONS`. The UI lists them from those tuples, not from a
   copy.

## In the app (Run → Decisions)
5. One row per candidate with its RDKit.js 2D structure, a status control, and a reason select
   populated from `GET /api/decisions/vocabulary` (which returns `STATUSES` and `REASONS`).
6. A refusal shows the core's message beside the row; nothing is written.
7. The grid is fully usable from the keyboard.
