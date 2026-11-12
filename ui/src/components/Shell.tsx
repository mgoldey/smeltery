import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { NavLink, useMatch, useSearchParams } from "react-router-dom";
import { api, runByRef, unwrap } from "../api/client";

/**
 * Where the page is: a run (its stable id), or a pipeline and the run it was opened from (`?run=<id>`). A pipeline
 * page is about a run as much as a run page is, so the rail and the context bar name both instead of saying "no run".
 */
function useLocation() {
  const runMatch = useMatch("/run/:runId/*");
  const reportMatch = useMatch("/report/:runId");
  const pipeMatch = useMatch("/pipelines/:name");
  const [search] = useSearchParams();
  const fromRun = pipeMatch ? search.get("run") : null;
  const runId = runMatch?.params.runId ?? reportMatch?.params.runId ?? fromRun ?? undefined;
  return { runId, runMatch, pipeline: pipeMatch?.params.name };
}

function useCurrentRun() {
  const { runId } = useLocation();
  const q = useQuery({
    queryKey: ["runs"],
    queryFn: () => unwrap(api.GET("/api/runs")),
    enabled: runId !== undefined,
  });
  return runByRef(q.data, runId);
}

export function ContextBar({ tabs }: { tabs?: ReactNode }) {
  const run = useCurrentRun();
  const { pipeline } = useLocation();
  return (
    <header className="context-bar" aria-label="Current run">
      {pipeline ? (
        <span className="campaign" data-testid="context-pipeline">
          Pipeline {pipeline}
          {run ? <span className="lede"> · from run</span> : null}
        </span>
      ) : null}
      {run ? (
        <>
          <span className="campaign" data-testid="context-campaign">
            {run.campaign}
          </span>
          <span className="digest mono" data-testid="context-digest" title={run.digest ?? ""}>
            {run.digest?.slice(0, 12)}
          </span>
        </>
      ) : pipeline ? (
        <span className="empty">not run yet</span>
      ) : (
        <span className="empty">No run selected</span>
      )}
      {tabs}
    </header>
  );
}

export function Shell({ children, tabs }: { children: ReactNode; tabs?: ReactNode }) {
  const who = useQuery({ queryKey: ["whoami"], queryFn: () => unwrap(api.GET("/api/whoami")) });
  const { runId, runMatch, pipeline } = useLocation();
  const [search] = useSearchParams();
  return (
    <div className="shell">
      <aside className="rail">
        <div className="brand">
          Bench<small>smeltery</small>
        </div>
        <nav aria-label="Areas">
          <NavLink to="/bench">Bench</NavLink>
          <NavLink to="/compare">Compare</NavLink>
          <NavLink to="/methods">Methods</NavLink>
          {pipeline ? (
            <NavLink to={`/pipelines/${pipeline}?${search.toString()}`} className="active" aria-current="page">
              Pipeline · {pipeline}
            </NavLink>
          ) : null}
          {runId !== undefined ? (
            <>
              <NavLink to={`/run/${runId}`} className={() => (runMatch ? "active" : "")}>
                Run
              </NavLink>
              <NavLink to={`/report/${runId}`}>Report</NavLink>
            </>
          ) : (
            <>
              <a aria-disabled="true">Run</a>
              <a aria-disabled="true">Report</a>
            </>
          )}
        </nav>
        <div className="who">{who.data ? `Signed in locally as ${who.data.user}` : " "}</div>
      </aside>
      <div className="main">
        <ContextBar tabs={tabs} />
        {children}
      </div>
    </div>
  );
}
