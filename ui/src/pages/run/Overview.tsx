import { useQuery } from "@tanstack/react-query";
import { api, unwrap } from "../../api/client";
import { DroppedPoses } from "../../components/DroppedPoses";
import { useRunId } from "./RunLayout";

export function Overview() {
  const runId = useRunId();
  const detail = useQuery({
    queryKey: ["run", runId],
    queryFn: () => unwrap(api.GET("/api/runs/{run_id}", { params: { path: { run_id: runId } } })),
  });
  const verdict = useQuery({
    queryKey: ["verdict", runId],
    queryFn: () => unwrap(api.GET("/api/runs/{run_id}/verdict", { params: { path: { run_id: runId } } })),
  });
  const drops = useQuery({
    queryKey: ["dropped_poses", runId],
    queryFn: () => unwrap(api.GET("/api/runs/{run_id}/dropped_poses", { params: { path: { run_id: runId } } })),
  });
  const d = detail.data;
  return (
    <div className="body">
      <main className="work">
        <h1>Provenance</h1>
        {detail.isError ? (
          <div className="banner" role="alert">
            {verdict.data?.state === "INCOMPLETE"
              ? `This run is incomplete: its record has no ${verdict.data.missing.join(", ")}. It stopped before the cut, so there is no result to show.`
              : `This run cannot be read: ${detail.error.message}`}
          </div>
        ) : null}
        {d ? (
          <>
            <p className="lede">
              {d.analogues.length} analogues of <strong>{d.parent}</strong>, cut at keep {d.keep} with z = {d.z}. The floor is{" "}
              <span className="num">
                {d.floor} {d.unit}
              </span>{" "}
              ({d.floor_source}).
            </p>
            <h2>Candidates</h2>
            <table className="grid">
              <thead>
                <tr>
                  <th>Candidate</th>
                  <th>SMILES</th>
                  <th className="num">Poses</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(d.candidates).map(([name, smiles]) => (
                  <tr key={name}>
                    <td>
                      {name}
                      {name === d.parent ? <span className="lede"> (parent)</span> : null}
                    </td>
                    <td className="mono">{smiles}</td>
                    <td className="num">{d.n_poses[name] ?? "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <DroppedPoses dropped={drops.data?.dropped} />
            <h2>Record</h2>
            <p className="mono">{d.path}</p>
            <p className="mono">input digest {d.digest}</p>
            <RunConfig runId={runId} />
          </>
        ) : null}
      </main>
    </div>
  );
}

/**
 * The config this run was executed from, as text.
 *
 * Provenance named the file and its hash but never showed it, so answering "what did this actually run?" meant
 * leaving the app for a shell. The comments come with it: `config_text` preserves them on purpose, and in this
 * project they are the documentation of why a round exists.
 */
function RunConfig({ runId }: { runId: string }) {
  const cfg = useQuery({
    queryKey: ["run_config", runId],
    queryFn: () => unwrap(api.GET("/api/runs/{run_id}/config", { params: { path: { run_id: runId } } })),
  });
  const c = cfg.data;
  if (!c) return null;
  return (
    <section aria-label="Config">
      <h2>Config</h2>
      {c.text === null ? (
        <p className="lede" data-testid="config-absent">
          {c.reason}
        </p>
      ) : (
        <>
          <p className="lede">
            <span className="mono">{c.file}</span>, sha256 <span className="mono">{c.sha256?.slice(0, 12)}</span>.
            This is the file as it is on disk, comments and all.
          </p>
          {c.changed ? (
            <div className="banner" role="alert" data-testid="config-changed">
              {c.reason}: the run is reproducible only from the bytes it read, so treat this as the current file
              rather than as provenance.
            </div>
          ) : null}
          <pre className="mono config-text" data-testid="config-text">
            {c.text}
          </pre>
        </>
      )}
    </section>
  );
}
