import type { RunSummary } from "../api/client";

/**
 * Telling two runs of one campaign apart, and finding the run a launch or a pipeline produced.
 *
 * `/api/runs` carries no date and no pipeline name: a `RunSummary` is `{id, path, campaign, parent, digest,
 * n_candidates, verdict, error}`. Two rounds of one campaign therefore differed only by digest and candidate
 * count in every list and picker, which is not something a person reads a run by.
 *
 * What the API does carry is `path`: the record file, `<out dir>/run-<digest12>.json`. The out directory is where
 * the run actually went, and Bench's own naming makes it the discriminator -- a launch defaults to
 * `out/<campaign>-<YYYYMMDD-HHMMSS>` (`api/pipelines.py`, `pipeline_launch`), and a pipeline's own runs sit under
 * `pipelines/<name>/`. So the out directory names the run, dates it when Bench chose the name, and says which
 * pipeline it belongs to. Everything below reads it; nothing here needs a route the API does not have.
 */

/** A run's out directory: its record file's parent, with no trailing slash. "" if the path has no separator. */
export function outDir(path: string): string {
  const cut = path.lastIndexOf("/");
  return cut < 0 ? "" : path.slice(0, cut);
}

/**
 * Segments of an out directory that name no particular run, so a label that stopped at one would say nothing.
 *
 * `out/` is the convention everywhere: `smeltery run --out out/...`, a launch's default parent, and the folder
 * `make_run_dir` nests under each run. `run/` is where a prebuilt example keeps the run it ships. A path ending in
 * either is named by what sits above it.
 */
const GENERIC = new Set(["out", "run", "runs", ".", ""]);

/**
 * The shortest tail of the out directory that still tells two runs apart: its last segment, extended leftwards
 * while that segment names no particular run.
 *
 * The last segment alone is not enough. Five runs under `<root>/<name>/out/` all end in `out`, so a label built
 * from it would print "out" five times -- the same defect as printing the campaign five times, one level down.
 */
export function outName(path: string): string {
  const parts = outDir(path).split("/");
  let i = parts.length - 1;
  while (i > 0 && GENERIC.has(parts[i]!)) i--;
  return parts.slice(i).join("/");
}

/**
 * The pipeline a run belongs to, from `pipelines/<name>/...` in its path, or null.
 *
 * Only a run written inside a pipeline's own folder is claimed. A run launched into `out/` names no pipeline, and
 * guessing one from its campaign would attach the wrong pipeline to it: runs "keep their own staged copy and
 * record no pipeline name" (`api/pipelines.py:562`), so this is the only mapping the data supports.
 */
export function pipelineOfRun(path: string): string | null {
  const m = /(?:^|\/)pipelines\/([^/]+)\//.exec(path);
  return m ? m[1]! : null;
}

/**
 * A date read out of a Bench-chosen out directory name: the `-YYYYMMDD-HHMMSS` a launch appends, as `YYYY-MM-DD`.
 *
 * Null for any other name. A directory the user typed carries no date, and inventing one from the file system
 * would be a different fact (when it was last written) wearing the label "when it ran".
 */
export function runDate(path: string): string | null {
  const m = /-(\d{4})(\d{2})(\d{2})-\d{6}$/.exec(outName(path));
  return m ? `${m[1]}-${m[2]}-${m[3]}` : null;
}

/**
 * One line that identifies a run among its siblings, for an option in a picker.
 *
 * Campaign first (what it is), then the out directory (which one), then the digest (which inputs). The digest
 * stays because it is what the run pages and the context bar show; it is no longer the only thing on offer.
 */
export function runLabel(r: Pick<RunSummary, "campaign" | "path" | "digest">): string {
  const where = outName(r.path) || r.path;
  const bits = [r.campaign ?? r.path, where];
  if (r.digest) bits.push(r.digest.slice(0, 12));
  return bits.join(" · ");
}

/**
 * The out directory a task's command names (`--out <dir>`), or undefined.
 *
 * `Task.run_id` cannot be used to find a Bench run: it is the Temporal workflow id. The command is the record of
 * where the run was told to write, and it is in the task row already.
 */
export function taskOut(command: string[]): string | undefined {
  const i = command.indexOf("--out");
  return i >= 0 ? command[i + 1] : undefined;
}

/**
 * Whether a run's record sits directly inside `out`.
 *
 * The record is a direct child, so the remainder after the directory must hold no further separator: a prefix test
 * alone would match every run nested under a typed-in parent directory such as `out/`, and the first of those
 * would be linked as "the result" of a run that has not finished.
 */
export function inOut(path: string, out: string | undefined): boolean {
  if (out === undefined || out === "") return false;
  const dir = out.endsWith("/") ? out : `${out}/`;
  return path.startsWith(dir) && !path.slice(dir.length).includes("/");
}

/** The run a task produced: the one whose record was written into the directory the task's command named. */
export function runOfTask(command: string[], runs: RunSummary[] | undefined): RunSummary | undefined {
  const out = taskOut(command);
  if (out === undefined) return undefined;
  return (runs ?? []).find((r) => inOut(r.path, out));
}

/**
 * The run to open a pipeline's step views on: the newest run inside that pipeline's folder.
 *
 * Without a `?run=`, Poses, Checks, ΔE_int and Cut all report that the step has not run -- including for an
 * example that ships with a finished run.
 *
 * "Newest" can only be read from a directory Bench dated itself. Among those, the latest date wins. Among the
 * rest -- `run/` from a copied example, or anything the user named -- there is no order to read, so the first in
 * the API's own (path-sorted) listing is taken and a dated run always beats an undated one. Sorting the names
 * against each other would be worse than arbitrary: `"run"` sorts above `"p-20261115-100000"`, so a copied
 * example's shipped run would outrank every later round of it.
 *
 * A run outside the pipeline's folder is never offered. Linking the wrong run would put another run's poses and
 * numbers under this pipeline's steps, which is worse than the view saying it has not run.
 */
export function latestRunOfPipeline(name: string, runs: RunSummary[] | undefined): RunSummary | undefined {
  const mine = (runs ?? []).filter((r) => pipelineOfRun(r.path) === name && !r.error);
  if (!mine.length) return undefined;
  const dated = mine.filter((r) => runDate(r.path) !== null);
  if (!dated.length) return mine[0];
  return dated.reduce((best, r) => (outName(r.path) > outName(best.path) ? r : best));
}

/** `/pipelines/<name>`, with `?run=` when that pipeline has a run of its own to show its steps from. */
export function pipelineHref(name: string, runs: RunSummary[] | undefined): string {
  const run = latestRunOfPipeline(name, runs);
  const at = `/pipelines/${encodeURIComponent(name)}`;
  return run ? `${at}?run=${encodeURIComponent(run.id)}` : at;
}
