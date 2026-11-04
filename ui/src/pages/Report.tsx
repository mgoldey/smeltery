import { useMutation, useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { api, unwrap } from "../api/client";
import { CommentThread } from "../components/CommentThread";
import { PlotlyFigure } from "../components/PlotlyFigure";
import { PoseExplorer } from "../components/PoseExplorer";
import { ProposeForm } from "../components/ProposeForm";
import { Shell } from "../components/Shell";
import { Structure2D } from "../components/Structure2D";
import { VerdictBanner } from "../components/VerdictBanner";

/**
 * What a bench chemist opens: read-only, the verdict first. They can comment on the run, a candidate or a pose,
 * and propose a candidate; nothing here edits the method or starts work.
 */
export function Report() {
  const idx = Number(useParams().idx);
  const verdict = useQuery({
    queryKey: ["verdict", idx],
    queryFn: () => unwrap(api.GET("/api/runs/{idx}/verdict", { params: { path: { idx } } })),
  });
  const ok = verdict.data !== undefined && verdict.data.state !== "INCOMPLETE";
  const detail = useQuery({
    queryKey: ["run", idx],
    queryFn: () => unwrap(api.GET("/api/runs/{idx}", { params: { path: { idx } } })),
    enabled: ok,
  });
  const table = useQuery({
    queryKey: ["ddE", idx],
    queryFn: () => unwrap(api.GET("/api/runs/{idx}/ddE", { params: { path: { idx } } })),
    enabled: ok,
  });
  const forest = useQuery({
    queryKey: ["figure", idx, "forest"],
    queryFn: () => unwrap(api.GET("/api/runs/{idx}/figures/{name}", { params: { path: { idx, name: "forest" } } })),
    enabled: ok,
  });
  const snapshot = useMutation({
    mutationFn: () => unwrap(api.POST("/api/runs/{idx}/snapshot", { params: { path: { idx } } })),
  });
  const d = detail.data;
  const sizes = new Map<number, number>();
  for (const r of table.data?.rows ?? []) sizes.set(r.group, (sizes.get(r.group) ?? 0) + 1);

  return (
    <Shell>
      <div className="body">
        <main className="work report">
          <VerdictBanner idx={idx} />
          {ok && forest.data ? (
            <section aria-label="Result plot">
              <h2>Paired ΔΔE against the parent</h2>
              <p className="lede">Lower is better. Candidates sharing a band could not be told apart; their order inside it means nothing.</p>
              <PlotlyFigure figure={forest.data} name="forest" />
            </section>
          ) : null}
          {d && table.data ? (
            <section aria-label="Candidates">
              <h2>Candidates</h2>
              <ul className="report-candidates">
                {table.data.rows.map((r) => (
                  <li key={r.candidate} className="report-candidate" data-candidate={r.candidate}>
                    <Structure2D smiles={r.smiles} />
                    <div>
                      <strong>{r.candidate}</strong>{" "}
                      <span className="num" data-testid={`mean-${r.candidate}`}>
                        {r.mean.toFixed(2)}
                      </span>{" "}
                      ± <span className="num">{r.sem.toFixed(2)}</span> {table.data.unit},{" "}
                      {(sizes.get(r.group) ?? 1) > 1 ? `tie group ${r.group + 1} (no order)` : `group ${r.group + 1}`}
                      {r.survivor === true ? ", kept" : r.survivor === false ? ", not kept" : ", unranked"}
                      <CommentThread idx={idx} anchor={`candidate:${r.candidate}`} title={r.candidate} />
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
                idx={idx}
                detail={d}
                label="report pose viewer"
                footer={(c, p) => <CommentThread idx={idx} anchor={`pose:${c}:${p}`} title={`pose ${p} of ${c}`} />}
              />
            </section>
          ) : null}
          <ProposeForm idx={idx} />
          <section aria-label="Discussion">
            <h2>Discussion</h2>
            <CommentThread idx={idx} anchor="run" title="this run" />
          </section>
          {ok ? (
            <section aria-label="Archive">
              <h2>Archive</h2>
              <p className="lede">
                A self-contained HTML copy for the notebook: every number, plot and table is inside the file. Its 2D and
                3D views load pinned, integrity-checked libraries and say so when they cannot.
              </p>
              <button type="button" onClick={() => snapshot.mutate()} disabled={snapshot.isPending}>
                Archive snapshot
              </button>
              {snapshot.isError ? <p className="field-error">{snapshot.error.message}</p> : null}
              {snapshot.data ? (
                <p role="status" data-testid="snapshot">
                  Written to <span className="mono">{snapshot.data.path}</span>, sha256{" "}
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
