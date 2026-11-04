# Frontend look and behaviour

**The app looks and behaves like a laboratory instrument.**

## Requirements
1. Colour tokens are CSS custom properties, redefined for dark mode: `--ground #F7F8F8`, `--panel`
   (a half-step lighter), `--hairline #D8DCDE`, `--ink #15181A`, `--accent #1B5E8C`. Structure
   comes from hairlines, not shadows or cards. No colour literal appears in a component; all colours
   come from tokens.
2. Data colours equal `smeltery.gui.scenes.CHARGE_POSITIVE`, `CHARGE_NEGATIVE` and
   `CANDIDATE_CARBON`. They are used only for data.
3. One humanist sans with real tabular figures (Source Sans 3 or IBM Plex Sans, bundled, not from a
   CDN). Every numeric cell has `font-variant-numeric: tabular-nums`. IBM Plex Mono only for code,
   SMILES, hashes and logs.
4. Layout: a fixed left rail (Bench, Run, Report); a context bar always showing the current run's
   campaign and digest; one work column; a right inspector that opens only when something is
   selected. Text is left-aligned and numbers are right-aligned.
5. The run's verdict (`verdict.md`) is the largest text on any run page.
6. No entrance animations and no hover lifts. Transitions only show what changed (inspector
   opening, a task row changing state). `prefers-reduced-motion` is respected.
7. Accessibility: axe reports no serious or critical violations on any page. Visible keyboard focus.
   The pipeline editor is fully usable without a mouse. Colour is never the only carrier of meaning.
8. Copy: buttons say what happens ("Run pipeline") and the confirmation uses the same verb
   ("Pipeline running"). Errors say what happened and what to do. An empty panel says what action
   fills it.
9. Text from runs, tasks and logs is rendered as text, never as HTML.
