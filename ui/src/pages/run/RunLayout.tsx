import { Suspense } from "react";
import { NavLink, Outlet, useParams } from "react-router-dom";
import { useCapabilities } from "../../api/capabilities";
import { Shell } from "../../components/Shell";

/** The Run area's tabs. A tab is listed here only once its page exists. */
export const RUN_TABS: { to: string; label: string }[] = [
  { to: "results", label: "Results" },
  { to: "poses", label: "Poses" },
  { to: "decisions", label: "Decisions" },
  { to: "proposals", label: "Proposals" },
  { to: "structure", label: "Structure" },
  { to: "pipeline", label: "Pipeline" },
  { to: "overview", label: "Overview" },
];

/** Tabs whose page only writes (or reads through a POST): not offered when the server is read-only. */
const WRITE_TABS = new Set(["decisions", "structure"]);

/** The run this page is about: the stable id from the URL (`/run/:runId`). An old all-digits link is passed through
 * unchanged -- the API still resolves it positionally and says so with a Deprecation header. */
export function useRunId(): string {
  const { runId } = useParams();
  if (!runId) throw new Error("no run in the URL");
  return runId;
}

export function RunLayout() {
  const { runId } = useParams();
  const { readOnly } = useCapabilities();
  const tabs = (
    <nav className="tabs" aria-label="Run views">
      {RUN_TABS.filter((t) => !(readOnly && WRITE_TABS.has(t.to))).map((t) => (
        <NavLink key={t.to} to={`/run/${runId}/${t.to}`}>
          {t.label}
        </NavLink>
      ))}
    </nav>
  );
  return (
    <Shell tabs={tabs}>
      <Suspense fallback={<p className="lede">Loading the view.</p>}>
        <Outlet />
      </Suspense>
    </Shell>
  );
}
