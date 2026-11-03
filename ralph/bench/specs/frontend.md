# Frontend

**The served app works offline and looks like an instrument.**

## Requirements

1. Offline. Mol\*, RDKit.js and the fonts are vendored into `src/smeltery/bench/static/vendor/` at
   pinned versions by `scripts/vendor_bench_assets.py`, which writes `vendor/README.md` with each
   URL, version and sha256. No served HTML references an `http(s)://` origin.
2. Plain ES modules served as files. No bundler, no build step.
3. Colour tokens are CSS custom properties in `static/app.css`, each redefined for dark mode:
   `--ground #F7F8F8`, `--panel` (a half-step lighter than ground), `--hairline #D8DCDE`,
   `--ink #15181A`, `--accent #1B5E8C`. Structure comes from hairlines, not shadows or cards.
4. Data colours are copied from `smeltery.gui.scenes`, not redefined: `CHARGE_POSITIVE`,
   `CHARGE_NEGATIVE`, `CANDIDATE_CARBON`. They are used only for data, never for decoration.
5. One humanist sans with real tabular figures (Source Sans 3 or IBM Plex Sans), self-hosted.
   `font-variant-numeric: tabular-nums` on every numeric cell. IBM Plex Mono only for literal code,
   SMILES, hashes and logs.
6. Layout: a fixed left rail (Bench / Run / Report); a context bar always showing the run's
   campaign and digest; one work column; a right inspector only when something is selected. Text
   left-aligned, numbers right-aligned.
7. The loudest element on any run page is its verdict (`verdict.md`). Everything else is quiet.
8. No entrance animations and no hover lifts. Transitions only where they show what changed.
   `prefers-reduced-motion` respected.
9. Visible keyboard focus everywhere; the pipeline editor is fully usable without a mouse; colour
   is never the only carrier of meaning; contrast meets WCAG AA.
10. Copy: buttons say what happens ("Run pipeline"), and the confirmation uses the same verb
    ("Pipeline running"). Errors say what happened and what to do, without apologising.
