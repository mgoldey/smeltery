import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { NavLink, useMatch } from "react-router-dom";
import { api, unwrap } from "../api/client";

function useCurrentRun() {
  const runMatch = useMatch("/run/:idx/*");
  const reportMatch = useMatch("/report/:idx");
  const idx = runMatch?.params.idx ?? reportMatch?.params.idx;
  const q = useQuery({
    queryKey: ["runs"],
    queryFn: () => unwrap(api.GET("/api/runs")),
    enabled: idx !== undefined,
  });
  return idx === undefined ? undefined : q.data?.find((r) => String(r.idx) === idx);
}

export function ContextBar({ tabs }: { tabs?: ReactNode }) {
  const run = useCurrentRun();
  return (
    <header className="context-bar" aria-label="Current run">
      {run ? (
        <>
          <span className="campaign" data-testid="context-campaign">
            {run.campaign}
          </span>
          <span className="digest mono" data-testid="context-digest" title={run.digest ?? ""}>
            {run.digest?.slice(0, 12)}
          </span>
        </>
      ) : (
        <span className="empty">No run selected</span>
      )}
      {tabs}
    </header>
  );
}

export function Shell({ children, tabs }: { children: ReactNode; tabs?: ReactNode }) {
  const who = useQuery({ queryKey: ["whoami"], queryFn: () => unwrap(api.GET("/api/whoami")) });
  const runMatch = useMatch("/run/:idx/*");
  const reportMatch = useMatch("/report/:idx");
  const idx = runMatch?.params.idx ?? reportMatch?.params.idx;
  return (
    <div className="shell">
      <aside className="rail">
        <div className="brand">
          Bench<small>smeltery</small>
        </div>
        <nav aria-label="Areas">
          <NavLink to="/bench">Bench</NavLink>
          <NavLink to="/compare">Compare</NavLink>
          {idx !== undefined ? (
            <>
              <NavLink to={`/run/${idx}`} className={() => (runMatch ? "active" : "")}>
                Run
              </NavLink>
              <NavLink to={`/report/${idx}`}>Report</NavLink>
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
