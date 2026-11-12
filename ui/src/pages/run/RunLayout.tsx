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

export function useRunIdx(): number {
  const { idx } = useParams();
  const n = Number(idx);
  if (!Number.isInteger(n) || n < 0) throw new Error(`not a run index: ${idx}`);
  return n;
}

export function RunLayout() {
  const { idx } = useParams();
  const { readOnly } = useCapabilities();
  const tabs = (
    <nav className="tabs" aria-label="Run views">
      {RUN_TABS.filter((t) => !(readOnly && WRITE_TABS.has(t.to))).map((t) => (
        <NavLink key={t.to} to={`/run/${idx}/${t.to}`}>
          {t.label}
        </NavLink>
      ))}
    </nav>
  );
  return (
    <Shell tabs={tabs}>
      <Outlet />
    </Shell>
  );
}
