import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, unwrap, type Schemas } from "../../api/client";

/**
 * Run the KEPT pipeline. Only Run is blocked by errors (a draft with errors is still kept): the button is disabled,
 * and says why, while the kept config has an error or the draft has edits that are not kept.
 */
export function Launch({ name, errors, dirty }: { name: string; errors: Schemas["Issue"][]; dirty: boolean }) {
  const qc = useQueryClient();
  const [out, setOut] = useState("");
  const launch = useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/pipelines/{name}/launch", { params: { path: { name } }, body: { out: out.trim() || null } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["tasks"] }),
  });
  const n = errors.length;
  const blocked = n > 0 || dirty;
  return (
    <section aria-label="Run this pipeline">
      <h2>Run</h2>
      <p className="lede">
        Runs the kept config. Every input file is copied into the run directory by hash and the config is written there,
        so the run can be redone from that directory alone.
      </p>
      <div className="controls">
        <div style={{ flex: "1 1 320px" }}>
          <label htmlFor="launch-out">Run directory (leave empty for a dated one under out/)</label>
          <input id="launch-out" className="mono" style={{ width: "100%" }} value={out} onChange={(e) => setOut(e.target.value)} />
        </div>
        <button className="primary" type="button" disabled={blocked || launch.isPending} onClick={() => launch.mutate()}>
          Run pipeline
        </button>
      </div>
      {n > 0 ? (
        <div className="field-error" data-testid="run-blocked">
          Run is blocked by {n} {n === 1 ? "error" : "errors"} in the kept pipeline:
          <ul>
            {errors.map((e, i) => (
              <li key={i}>
                {e.stage ? <span className="mono">{e.stage}: </span> : null}
                {e.message}
              </li>
            ))}
          </ul>
        </div>
      ) : dirty ? (
        <p className="field-error" data-testid="run-blocked">
          Keep or discard the draft to run: Run uses the kept config.
        </p>
      ) : null}
      {launch.isError ? (
        <p className="field-error" role="alert">
          {launch.error.message}
        </p>
      ) : null}
      {launch.data ? (
        <p role="status" data-testid="launched">
          Pipeline {launch.data.status === "queued" ? "queued (waiting for a run slot)" : "running"} as task <span className="mono">{launch.data.id}</span>:{" "}
          <span className="mono">{launch.data.command.join(" ")}</span>.{" "}
          {/* To the task itself, not just to the page that contains it: the Work panel sits below the fold, so a
              bare /bench link left the user hunting for the thing they had just started. */}
          <Link to={`/bench?task=${encodeURIComponent(launch.data.id)}`}>Follow it in Work</Link>.{" "}
          <OpenResult command={launch.data.command} />
        </p>
      ) : null}
    </section>
  );
}


/**
 * The end of the loop's first leg: the run this launch produced.
 *
 * Launching used to end at "Follow it in Work", which is the task, not the result -- the user watched a row turn
 * green and then had to find the new run in a table of runs that look alike. A finished run writes its record into
 * the out directory the command names, so the run store lists it under that path; `Task.run_id` cannot be used,
 * since it is the Temporal workflow id, not a Bench run.
 */
/**
 * A run's `path` is its record file directly inside the out directory.
 *
 * The record is a direct child, so the remainder after the directory must hold no further separator: a prefix
 * test alone would match every run nested under a typed-in parent directory such as `out/`, and the first of
 * those would be linked as "the result" of a run that has not finished.
 */
function inOut(path: string, out: string | undefined): boolean {
  if (out === undefined) return false;
  const dir = out.endsWith("/") ? out : `${out}/`;
  return path.startsWith(dir) && !path.slice(dir.length).includes("/");
}

type RunRow = { path: string; id: string };

function OpenResult({ command }: { command: string[] }) {
  const qc = useQueryClient();
  const i = command.indexOf("--out");
  const out = i >= 0 ? command[i + 1] : undefined;
  // Runs that already existed when this launch started. An out directory the user typed may already hold a
  // record (another run's, or an earlier attempt's), and linking to that one would show the wrong numbers
  // under the word "result". Only a run that appears after the launch is this launch's.
  const [before] = useState(() => new Set((qc.getQueryData(["runs"]) as RunRow[] | undefined)?.map((r) => r.path)));
  const [until] = useState(() => Date.now() + 60 * 60 * 1000);
  const isMine = (r: RunRow) => inOut(r.path, out) && !before.has(r.path);
  // Polls while the run is being written; stops as soon as the run store lists it.
  const runs = useQuery({
    queryKey: ["runs"],
    queryFn: () => unwrap(api.GET("/api/runs")),
    enabled: out !== undefined,
    // A run that fails never writes a record, so "poll until it appears" would poll for as long as the page
    // is open. Stop after a window long enough for an ordinary run; the Work panel is where a failure is read.
    refetchInterval: (q) => (q.state.data?.some(isMine) || Date.now() > until ? false : 5000),
  });
  const mine = out === undefined ? undefined : runs.data?.find(isMine);
  if (!mine) {
    return (
      <span className="lede">
        The result appears here when the run finishes. If it fails, the Work panel says why.
      </span>
    );
  }
  return (
    <Link className="button primary" to={`/run/${mine.id}/results`} data-testid="open-result">
      Open the result
    </Link>
  );
}
