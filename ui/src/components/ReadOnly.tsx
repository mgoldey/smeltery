import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { useCapabilities } from "../api/capabilities";
import { Shell } from "./Shell";

/** Children that write (Run, Edit, Import, Delete, record a decision): absent on a read-only server, which would refuse them. */
export function Writable({ children }: { children: ReactNode }) {
  return useCapabilities().readOnly ? null : <>{children}</>;
}

/** A page that exists only to write: on a read-only server it is replaced by a sentence saying so. */
export function NeedsWrite({ what, children }: { what: string; children: ReactNode }) {
  if (!useCapabilities().readOnly) return <>{children}</>;
  return (
    <Shell>
      <div className="body">
        <main className="work">
          <h1>{what}</h1>
          <p className="lede" data-testid="read-only-page">
            This Bench is read-only, so {what.toLowerCase()} cannot be changed here. Results, poses, reports and
            lineage are still readable: <Link to="/bench">back to Bench</Link>.
          </p>
        </main>
      </div>
    </Shell>
  );
}
