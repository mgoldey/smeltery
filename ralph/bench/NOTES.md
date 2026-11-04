# Bench loop notes

Things a future iteration needs and cannot get from the code or `git log`. One line each, newest
last. Not a diary: if it does not change what the next iteration does, leave it out.

- 2026-11-03: features.json was written after a review of the first implementation pass. That pass
  left a circular atom-order test, a queue race reporting exit-0 tasks as failed, and a duplicate
  `bench/application/` package. The R-features repair those first.
- 2026-11-03 (R1): `bench/application/` was already deleted (untracked, so no history) before R1
  ran; its diverged edit_pipeline/launch_run/read_run/propose_candidate could not be diffed. The
  use_cases/ copies are the only ones left; the U-features own their behaviour. Until R2 and R11
  pass, the bench gate fails on the SubprocessTaskQueue returncode test and ruff fails on
  tests/bench: those failures are pre-existing, not yours.
- 2026-11-03 (M1): full suite 1723 passed, 45 skipped, 0 failed (baseline 1649 passed, 45 skipped).
- 2026-11-03 (M2): full suite 1723 passed, 45 skipped, 0 failed; cli row tests 119 passed, 2 skipped.
- 2026-11-03 (M3): full suite 1789 passed, 45 skipped, 0 failed.
- 2026-11-03 (M1): full suite after repairs: 1723 passed, 45 skipped, 0 failed.
- 2026-11-03 (K5): 10-candidate split peak RSS (kbytes): 144440 144376 144648 144384 144800 144644 144028 143408 144556 144868 112204.
- 2026-11-03 (M2): full suite after core changes: 1735 passed, 45 skipped, 1 known flake (danuglipron failed SCF).
- 2026-11-03 (M3): full suite: 1735 passed, 45 skipped, 1 known flake (danuglipron failed SCF).
- 2026-11-03 (M1): full suite 1723 passed, 45 skipped (baseline 1649 passed, 45 skipped).
- 2026-11-03 (M2): full suite 1723 passed, 45 skipped.
