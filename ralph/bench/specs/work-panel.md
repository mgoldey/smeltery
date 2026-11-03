# Work panel

**The computational chemist sees and cancels running work.**

## Requirements

1. `GET /work` lists tasks from `TaskQueue`: name, status, elapsed, exit code, log tail.
2. Each status shows its word as well as its colour.
3. `POST /api/tasks/{id}/cancel` cancels; an unknown id is 404.
4. The panel states that a task runs at most once and a dead worker's task is reported failed, not
   retried.
5. With the queue unreachable, the panel says so with the reason; the rest of the page still loads.
