import { useMutation, useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { api, unwrap } from "../api/client";
import { CommentThread } from "../components/CommentThread";
import { PlotlyFigure } from "../components/PlotlyFigure";
import { useScheme } from "../styles/scheme";
import { PoseExplorer } from "../components/PoseExplorer";
import { useCanWrite } from "../api/capabilities";
import { Writable } from "../components/ReadOnly";
import { ProposeForm } from "../components/ProposeForm";
import { Shell } from "../components/Shell";
import { Structure2D } from "../components/Structure2D";
import { VerdictBanner } from "../components/VerdictBanner";

/**
 * What a bench chemist opens: read-only, the verdict first. They can comment on the run, a candidate or a pose,
 * and propose a candidate; nothing here edits the method or starts work.
 */
export function Report() {
  const runId = useParams().runId ?? "";
  const verdict = useQuery({
    queryKey: ["verdict", runId],
    queryFn: () =>
      unwrap(
        api.GET("/api/runs/{run_id}/verdict", {
          params: { path: { run_id: runId } },
        }),
      ),
  });
  const ok = verdict.data !== undefined && verdict.data.state !== "INCOMPLETE";
  const detail = useQuery({
    queryKey: ["run", runId],
    queryFn: () =>
      unwrap(
        api.GET("/api/runs/{run_id}", { params: { path: { run_id: runId } } }),
      ),
    enabled: ok,
  });
  const table = useQuery({
    queryKey: ["ddE", runId],
    queryFn: () =>
      unwrap(
        api.GET("/api/runs/{run_id}/ddE", {
          params: { path: { run_id: runId } },
        }),
      ),
    enabled: ok,
  });
  const theme = useScheme();
  const forest = useQuery({
    queryKey: ["figure", runId, "forest", theme],
    queryFn: () =>
      unwrap(
        api.GET("/api/runs/{run_id}/figures/{name}", {
          params: { path: { run_id: runId, name: "forest" }, query: { theme } },
        }),
      ),
    enabled: ok,
  });
  const canWrite = useCanWrite();
  const snapshot = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/runs/{run_id}/snapshot", {
          params: { path: { run_id: runId } },
        }),
      ),
  });
  const limits = useQuery({
    queryKey: ["limits"],
    queryFn: () => unwrap(api.GET("/api/limits")),
  });
  const d = detail.data;
  const sizes = new Map<number, number>();
  for (const r of table.data?.rows ?? [])
    sizes.set(r.group, (sizes.get(r.group) ?? 0) + 1);

  return (
    <Shell>
      <div className="body">
        <main className="work report">
          <VerdictBanner runId={runId} />
          {d ? (
            <p className="lede" data-testid="floor-statement">
              {d.floor_statement}
            </p>
          ) : null}
          {ok && forest.data ? (
            <section aria-label="Result plot">
              <h2>Paired ΔΔE against the parent</h2>
              <p className="lede">
                Lower is better. Candidates sharing a band could not be told
                apart; their order inside it means nothing.
              </p>
              <PlotlyFigure figure={forest.data} name="forest" />
            </section>
          ) : null}
          {d && table.data ? (
            <section aria-label="Candidates">
              <h2>Candidates</h2>
              <ul className="report-candidates">
                {table.data.rows.map((r) => (
                  <li
                    key={r.candidate}
                    className="report-candidate"
                    data-candidate={r.candidate}
                  >
                    <Structure2D smiles={r.smiles} />
                    <div>
                      <strong>{r.candidate}</strong>{" "}
                      <span className="num" data-testid={`mean-${r.candidate}`}>
                        {r.mean.toFixed(2)}
                      </span>{" "}
                      ± <span className="num">{r.sem.toFixed(2)}</span>{" "}
                      {table.data.unit},{" "}
                      {(sizes.get(r.group) ?? 1) > 1
                        ? `tie group ${r.group + 1} (no order)`
                        : `group ${r.group + 1}`}
                      {r.survivor === true
                        ? ", kept"
                        : r.survivor === false
                          ? ", not kept"
                          : ", unranked"}
                      <CommentThread
                        runId={runId}
                        anchor={`candidate:${r.candidate}`}
                        title={r.candidate}
                      />
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
          {d ? (
            <section aria-label="Poses">
              <h2>Poses</h2>
              <PoseExplorer
                runId={runId}
                detail={d}
                label="report pose viewer"
                footer={(c, p) => (
                  <CommentThread
                    runId={runId}
                    anchor={`pose:${c}:${p}`}
                    title={`pose ${p} of ${c}`}
                  />
                )}
              />
            </section>
          ) : null}
          <Writable>
            <ProposeForm runId={runId} />
          </Writable>
          <section aria-label="Discussion">
            <h2>Discussion</h2>
            <CommentThread runId={runId} anchor="run" title="this run" />
          </section>
          {limits.data ? (
            <section aria-label="What this result cannot tell you">
              <details>
                <summary>
                  <h2 style={{ display: "inline" }}>
                    What this result cannot tell you
                  </h2>
                </summary>
                <p className="lede">
                  Measured limits of the funnel (docs/limits.md). Each rests on
                  one system or one model pair.
                </p>
                <table className="grid">
                  <thead>
                    <tr>
                      <th>ID</th>
                      <th>Limit</th>
                      <th>Magnitude</th>
                      <th>Claim it forbids</th>
                    </tr>
                  </thead>
                  <tbody>
                    {limits.data.measured.map((l) => (
                      <tr key={l.id}>
                        <td>{l.id}</td>
                        <td>{l.limit}</td>
                        <td>{l.magnitude}</td>
                        <td>{l.forbids}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <h3>Not yet measured</h3>
                <table className="grid">
                  <thead>
                    <tr>
                      <th>ID</th>
                      <th>Limit</th>
                      <th>Status</th>
                      <th>Claim it forbids until measured</th>
                    </tr>
                  </thead>
                  <tbody>
                    {limits.data.unmeasured.map((l) => (
                      <tr key={l.id}>
                        <td>{l.id}</td>
                        <td>{l.limit}</td>
                        <td>{l.status}</td>
                        <td>{l.forbids}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </details>
            </section>
          ) : null}
          {ok && canWrite ? (
            <section aria-label="Archive">
              <h2>Archive</h2>
              <p className="lede">
                A self-contained HTML copy for the notebook: every number, plot
                and table is inside the file. Its 2D and 3D views load pinned,
                integrity-checked libraries and say so when they cannot.
              </p>
              <button
                type="button"
                onClick={() => snapshot.mutate()}
                disabled={snapshot.isPending}
              >
                Archive snapshot
              </button>
              {snapshot.isError ? (
                <p className="field-error">{snapshot.error.message}</p>
              ) : null}
              {snapshot.data ? (
                <p role="status" data-testid="snapshot">
                  Written to <span className="mono">{snapshot.data.path}</span>,
                  sha256{" "}
                  <span className="mono" data-testid="snapshot-sha">
                    {snapshot.data.sha256}
                  </span>
                  .
                </p>
              ) : null}
            </section>
          ) : null}
        </main>
      </div>
    </Shell>
  );
}
