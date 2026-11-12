import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { useCanWrite, useCapabilities } from "../api/capabilities";
import { Shell } from "./Shell";

/**
 * Children that write (Run, Edit, Import, Delete, record a decision): absent on a read-only server, which would
 * refuse them. Absent while `/api/whoami` is still in flight too -- a control that appears and then vanishes is
 * worse than one that appears a moment late, and on a read-only server the early version is a lie.
 */
export function Writable({ children }: { children: ReactNode }) {
  return useCanWrite() ? <>{children}</> : null;
}

/** A page that exists only to write: on a read-only server it is replaced by a sentence saying so. */
export function NeedsWrite({
  what,
  children,
}: {
  what: string;
  children: ReactNode;
}) {
  // Not useCanWrite(): until the server answers, render the page rather than claiming it is read-only. A writable
  // server is the common case, and this page's own writes fail loudly if that guess is wrong.
  if (!useCapabilities().readOnly) return <>{children}</>;
  return (
    <Shell>
      <div className="body">
        <main className="work">
          <h1>{what}</h1>
          <p className="lede" data-testid="read-only-page">
            This Bench is read-only, so {what.toLowerCase()} cannot be changed
            here. Results, poses, reports and lineage are still readable:{" "}
            <Link to="/bench">back to Bench</Link>.
          </p>
        </main>
      </div>
    </Shell>
  );
}
