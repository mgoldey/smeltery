import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { hasResult } from "../api/client";
import { CommentThread } from "../components/CommentThread";
import { cutOutcome, groupLabel, groupSizes } from "../components/cut";
import { PlotlyFigure } from "../components/PlotlyFigure";
import { useScheme } from "../styles/scheme";
import { PoseExplorer } from "../components/PoseExplorer";
import { Writable } from "../components/ReadOnly";
import { ProposeForm } from "../components/ProposeForm";
import { Limits } from "../components/Limits";
import { Shell } from "../components/Shell";
import { Structure2D } from "../components/Structure2D";
import { VerdictBanner } from "../components/VerdictBanner";
import { ddeQuery, figureQuery, runQuery, verdictQuery } from "../api/queries";

/**
 * What a bench chemist opens: read-only, the verdict first. They can comment on the run, a candidate or a pose,
 * and propose a candidate; nothing here edits the method or starts work.
 */
export function Report() {
  const runId = useParams().runId ?? "";
  const verdict = useQuery(verdictQuery(runId));
  const ok = hasResult(verdict.data);
  const detail = useQuery({ ...runQuery(runId), enabled: ok });
  const table = useQuery({ ...ddeQuery(runId), enabled: ok });
  const theme = useScheme();
  const forest = useQuery({ ...figureQuery(runId, "forest", theme), enabled: ok });
  const d = detail.data;
  const sizes = groupSizes(table.data?.rows ?? []);

  return (
    <Shell>
      <div className="body">
        <main className="work report">
          {/* The floor statement is printed just below, under its own testid, so the banner does not repeat it. */}
          <VerdictBanner runId={runId} floorStatement={false} />
          {/* This page is often the whole app for someone sent a link: a section that silently disappears on a
              failed fetch reads as "the run has none of that", so each failure says so instead. */}
          {detail.isError ? (
            <div className="banner" role="alert">
              The run record could not be read: {detail.error.message}. The floor statement and the poses below are
              missing for that reason, not because the run lacks them. Reload to try again.
            </div>
          ) : null}
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
          {forest.isError ? (
            <p className="empty-state">
              The forest plot could not be drawn: {forest.error.message}
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
          {table.isError ? (
            <section aria-label="Candidates">
              <h2>Candidates</h2>
              <div className="banner" role="alert">
                The paired ΔΔE table could not be read: {table.error.message}.
                This run's candidates cannot be listed here; reload to try
                again.
              </div>
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
                      {groupLabel(r.group, sizes.get(r.group) ?? 1)},{" "}
                      {cutOutcome(r.survivor)}
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
          {detail.isError ? (
            <section aria-label="Poses">
              <h2>Poses</h2>
              <p className="empty-state">
                The poses could not be loaded: {detail.error.message}
              </p>
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
            {/* A proposal is recorded and then goes quiet: the store only adds and lists, so nothing marks one
                accepted, rejected or seen, and no notification goes anywhere. Saying where it landed and that the
                outcome has to be asked for is the honest version -- the alternative is "waiting for review" and a
                reader who waits. A real lifecycle needs a status on the proposal store and a route to set it. */}
            <p className="lede" data-testid="proposal-afterwards">
              A proposal lands in this run's{" "}
              <Link to={`/run/${runId}/proposals`}>review queue</Link>, where the computational chemist can take it
              into a next round. Nothing here reports back when they do: the queue records who proposed what, and
              not what was decided about it, so ask them if you need to know.
            </p>
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
