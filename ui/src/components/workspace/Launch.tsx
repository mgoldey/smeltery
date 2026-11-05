import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, unwrap } from "../../../api/client";

/** Launch the pipeline as a run. Refused (and disabled) while any blocking issue remains. */
export function Launch({ toml, ok, blocking }: { toml: string; ok: boolean; blocking: number }) {
  const qc = useQueryClient();
  const [out, setOut] = useState("");
  const launch = useMutation({
    mutationFn: () => unwrap(api.POST("/api/pipeline/launch", { body: { toml, out: out.trim() || null } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["tasks"] }),
  });
  return (
    <section aria-label="Run this pipeline">
      <h2>Run</h2>
      <p className="lede">
        Launching copies every input file into the run directory by hash and writes the config there, so the run can be
        reproduced from that directory alone.
      </p>
      <div className="controls">
        <div style={{ flex: "1 1 360px" }}>
          <label htmlFor="launch-out">Run directory (leave empty for a dated one under out/)</label>
          <input id="launch-out" className="mono" style={{ width: "100%" }} value={out} onChange={(e) => setOut(e.target.value)} />
        </div>
        <button className="primary" type="button" disabled={!ok || launch.isPending} onClick={() => launch.mutate()}>
          Run pipeline
        </button>
      </div>
      {!ok ? (
        <p className="field-error">
          {blocking} blocking {blocking === 1 ? "issue" : "issues"} above: fix {blocking === 1 ? "it" : "them"} to run.
        </p>
      ) : null}
      {launch.isError ? (
        <p className="field-error" role="alert">
          {launch.error.message}
        </p>
      ) : null}
      {launch.data ? (
        <p role="status" data-testid="launched">
          Pipeline running as task <span className="mono">{launch.data.id}</span>:{" "}
          <span className="mono">{launch.data.command.join(" ")}</span>. <Link to="/bench">Follow it in Work</Link>.
        </p>
      ) : null}
    </section>
  );
}
