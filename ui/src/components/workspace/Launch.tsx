import { useMutation, useQueryClient } from "@tanstack/react-query";
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
          <span className="mono">{launch.data.command.join(" ")}</span>. <Link to="/bench">Follow it in Work</Link>.
        </p>
      ) : null}
    </section>
  );
}
