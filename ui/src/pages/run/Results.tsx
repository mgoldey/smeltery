import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, unwrap } from "../../api/client";
import { LineageInspector } from "../../components/LineageInspector";
import { EmptyState } from "../../components/EmptyState";
import { PerPoseTable } from "../../components/PerPoseTable";
import { PlotlyFigure } from "../../components/PlotlyFigure";
import { VerdictBanner } from "../../components/VerdictBanner";
import { useScheme } from "../../styles/scheme";
import { useRunId } from "./RunLayout";

function useFigure(runId: string, name: "forest" | "matrix" | "per_pose" | "per_pose_values", enabled: boolean) {
  const theme = useScheme();
  return useQuery({
    queryKey: ["figure", runId, name, theme],
    queryFn: () =>
      unwrap(api.GET("/api/runs/{run_id}/figures/{name}", { params: { path: { run_id: runId, name }, query: { theme } } })),
    enabled,
  });
}

function groupLabel(group: number, size: number): string {
  return size > 1 ? `tie group ${group + 1} (no order)` : `group ${group + 1}`;
}

export function Results() {
  const runId = useRunId();
  const [traced, setTraced] = useState<string | null>(null);
  const [pairs, setPairs] = useState(true);
  const verdict = useQuery({
    queryKey: ["verdict", runId],
    queryFn: () => unwrap(api.GET("/api/runs/{run_id}/verdict", { params: { path: { run_id: runId } } })),
  });
  const ok = verdict.data !== undefined && verdict.data.state !== "INCOMPLETE";
  const table = useQuery({
    queryKey: ["ddE", runId],
    queryFn: () => unwrap(api.GET("/api/runs/{run_id}/ddE", { params: { path: { run_id: runId } } })),
    enabled: ok,
  });
  const forest = useFigure(runId, "forest", ok);
  const matrix = useFigure(runId, "matrix", ok);
  const perPose = useFigure(runId, "per_pose", ok);
  const perPoseValues = useFigure(runId, "per_pose_values", ok && !pairs);
  const rows = useQuery({
    queryKey: ["per_pose", runId],
    queryFn: () => unwrap(api.GET("/api/runs/{run_id}/per_pose", { params: { path: { run_id: runId } } })),
    enabled: ok,
  });
  const sizes = new Map<number, number>();
  for (const r of table.data?.rows ?? []) sizes.set(r.group, (sizes.get(r.group) ?? 0) + 1);

  return (
    <div className="body">
      <main className="work">
        <VerdictBanner runId={runId} />
        {ok && table.data ? (
          <>
            <h2>Paired ΔΔE against the parent</h2>
            <p className="lede">
              Mean ± {table.data.z}·SEM per analogue. Candidates the cut could not separate share a band and a label; their
              order inside it means nothing.
            </p>
            {table.data.rows.length === 0 ? (
              <EmptyState action={{ label: "Open the run overview", to: `/run/${runId}/overview` }}>
                No analogue has a paired ΔΔE in this run; the parent alone gives nothing to compare.
              </EmptyState>
            ) : null}
            {forest.data && table.data.rows.length ? <PlotlyFigure figure={forest.data} name="forest" /> : null}
            {table.data.rows.length ? (
              <table className="grid" aria-label="Paired ΔΔE">
                <thead>
                  <tr>
                    <th>Candidate</th>
                    <th className="num">Mean ({table.data.unit})</th>
                    <th className="num">SEM</th>
                    <th className="num">Interval</th>
                    <th className="num">Poses</th>
                    <th>Group</th>
                    <th>Cut</th>
                  </tr>
                </thead>
                <tbody>
                  {table.data.rows.map((r) => (
                    <tr key={r.candidate}>
                      <td>
                        <button
                          type="button"
                          className="link"
                          aria-label={`Trace ${r.candidate}`}
                          aria-pressed={traced === r.candidate}
                          onClick={() => setTraced(r.candidate)}
                        >
                          {r.candidate}
                        </button>
                      </td>
                      <td className="num">{r.mean.toFixed(2)}</td>
                      <td className="num">{r.sem.toFixed(2)}</td>
                      <td className="num">
                        {r.lo.toFixed(2)} to {r.hi.toFixed(2)}
                      </td>
                      <td className="num">{r.n}</td>
                      <td>{groupLabel(r.group, sizes.get(r.group) ?? 1)}</td>
                      <td>{r.survivor === null ? "unranked" : r.survivor ? "kept" : "not kept"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : null}
            <h2>Can these two be told apart?</h2>
            <p className="lede">The funnel's own test for each pair, at the same z and floor as the cut.</p>
            {matrix.data && table.data.rows.length ? <PlotlyFigure figure={matrix.data} name="matrix" /> : null}
            <h2>Per-pose pairs</h2>
            <div className="controls" role="group" aria-label="Per-pose view">
              <button type="button" aria-pressed={pairs} onClick={() => setPairs(true)}>
                Paired lines
              </button>
              <button type="button" aria-pressed={!pairs} onClick={() => setPairs(false)}>
                Values per pose
              </button>
            </div>
            {pairs ? (
              <>
                <p className="lede">Each line joins an analogue's pose to the parent's pose with the same index; pairing is why the paired SEM is small.</p>
                {perPose.isError ? <p className="empty-state">{perPose.error.message}</p> : null}
                {perPose.data && table.data.rows.length ? <PlotlyFigure figure={perPose.data} name="per_pose" /> : null}
              </>
            ) : (
              <>
                <p className="lede">The parent's and each analogue's own value at every pose, before pairing.</p>
                {perPoseValues.isError ? <p className="empty-state">{perPoseValues.error.message}</p> : null}
                {perPoseValues.data && table.data.rows.length ? <PlotlyFigure figure={perPoseValues.data} name="per_pose_values" /> : null}
              </>
            )}
            {rows.isError && !(pairs ? perPose.isError : perPoseValues.isError) ? <p className="empty-state">{rows.error.message}</p> : null}
            {rows.data ? <PerPoseTable rows={rows.data} unit={table.data.unit} fileName={`per_pose_${runId}.csv`} /> : null}
          </>
        ) : null}
      </main>
      {traced !== null ? <LineageInspector runId={runId} candidate={traced} onClose={() => setTraced(null)} /> : null}
    </div>
  );
}
