# Work panel

**The computational chemist sees and cancels running work.**

## Requirements

1. `GET /work` lists tasks from `TaskQueue`: name, status, elapsed, exit code, log tail.
2. Each status shows its word as well as its colour.
3. `POST /api/tasks/{id}/cancel` cancels; an unknown id is 404.
4. The panel states that a task runs at most once and a dead worker's task is reported failed, not
   retried.
5. With the queue unreachable, the panel says so with the reason; the rest of the page still loads.

## In the app (Bench area)
6. The work panel is a live table: status refreshes without a page reload while any task is
   queued or running.
7. Elapsed time is computed from `started_at` to `finished_at` (or now), not the start timestamp.
8. Selecting a task opens its log tail in the inspector, rendered as text (a task printing `<b>`
   shows the characters `<b>`).
9. Status colours come from tokens; each status also shows its word.
10. With the queue unreachable, a banner gives the reason and the rest of the app keeps working.
