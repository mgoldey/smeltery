import type { ReactNode } from "react";
import { Link } from "react-router-dom";

type Action =
  { label: string; to: string } | { label: string; onClick: () => void };

/** An empty list says what to do next: one sentence, and a link or button when there is somewhere sensible to go. */
export function EmptyState({
  children,
  action,
}: {
  children: ReactNode;
  action?: Action;
}) {
  return (
    <p className="empty-state" data-testid="empty-state">
      {children}
      {action ? (
        <>
          {" "}
          {"to" in action ? (
            <Link to={action.to}>{action.label}</Link>
          ) : (
            <button type="button" onClick={action.onClick}>
              {action.label}
            </button>
          )}
        </>
      ) : null}
    </p>
  );
}
