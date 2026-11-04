# Bench build loop

You are one iteration of a loop. You remember nothing from earlier iterations; the files and git
history are the memory. Do exactly one feature, then stop.

1. Read `AGENTS.md`, then `ralph/bench/NOTES.md`, then `git log --oneline -15`.
2. Open `ralph/bench/features.json`. Your feature is the first one with `"passes": false` whose
   `depends_on` features all pass. Work on nothing else.
3. Read `ralph/bench/specs/README.md` and the one spec named in the feature's `spec` field.
4. Search before writing (`rg` for the names involved). Do not assume something is missing, and do
   not create a second home for something that already exists.
5. Implement it completely. No stubs, no placeholders, no TODOs.
6. Do every step in `steps`, in order:
   - When a step names a data source, the test reads that source. Deriving the expected value the
     same way the code does is circular and does not count.
   - A mutation step means: make the stated break, confirm the named test fails, revert the break.
     If the test still passes, the test is wrong: fix the test.
7. Run the feature's `verify` command, then the gates in `AGENTS.md`. Run the full suite only if
   the feature has `"full_suite": true`.
8. In `features.json`, change `passes` to `true` for this one feature. Never edit any other field,
   and never add or remove features. If a feature is wrong or impossible, say why in `NOTES.md`,
   leave `passes` false, and end the iteration.
9. Commit only the paths you changed (`git add <paths>`), with the message `bench(<id>): <summary>`.
10. Add a line to `NOTES.md` only if a future iteration needs to know it.
11. End your turn.

If every feature in `features.json` has `"passes": true`, output <promise>COMPLETE</promise> as
your final line. If the repository cannot be built or tested at all, explain why and output
<promise>ABORT</promise> as your final line.
