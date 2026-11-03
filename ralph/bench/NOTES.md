# Bench loop notes

Things a future iteration needs and cannot get from the code or `git log`. One line each, newest
last. Not a diary: if it does not change what the next iteration does, leave it out.

- 2026-11-03: features.json was written after a review of the first implementation pass. That pass
  left a circular atom-order test, a queue race reporting exit-0 tasks as failed, and a duplicate
  `bench/application/` package. The R-features repair those first.
