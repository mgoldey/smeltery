import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { api, unwrap } from "../api/client";
import { CommentThread } from "../components/CommentThread";
import { PlotlyFigure } from "../components/PlotlyFigure";
import { useScheme } from "../styles/scheme";
import { PoseExplorer } from "../components/PoseExplorer";
import { Writable } from "../components/ReadOnly";
import { ProposeForm } from "../components/ProposeForm";
import { Limits } from "../components/Limits";
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
          {ok ? (
            <p className="next-step">
              <a
                className="button"
                href={`/api/runs/${runId}/report.html`}
                download
                data-testid="download-report"
              >
                Download this report
              </a>{" "}
              <span className="lede">
                One self-contained HTML file for the notebook: every number,
                plot and table is inside it, and its 2D and 3D views load
                pinned, integrity-checked libraries and say so when they
                cannot.
              </span>
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
          <Limits />
        </main>
      </div>
    </Shell>
  );
}
