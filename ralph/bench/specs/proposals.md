# Proposals

**A bench chemist proposes a candidate without causing any compute.**

## Requirements

1. `smeltery.project.Proposal`: `id`, `run`, `smiles`, `author`, `reason` (a closed set), `note`,
   `created_at`, with `problems()` and `validate()` like `Decision`.
2. `POST /api/runs/{idx}/proposals {"smiles","reason","note"}` stores a proposal for the
   computational chemist's review queue and returns 201.
3. **A proposal never submits a task.** After a proposal is created, the task queue has received
   nothing. `Proposal` has no method whose name starts with submit, launch, run, execute or spawn.
4. An invalid SMILES or an unknown reason is 400.
