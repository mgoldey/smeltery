import { useQuery } from "@tanstack/react-query";
import { api, unwrap } from "../api/client";

/**
 * What the funnel cannot tell you: the measured limits (docs/limits.md) and the ones still unmeasured.
 *
 * One component, two places, because the two audiences need it at different moments. On Methods it is the frame
 * you read before choosing a method; on Report it is the caveat you read beside a verdict. Duplicating the table
 * would let the two drift, and a limit that is stated differently in two places is worse than one stated once.
 */
export function Limits({ open = false, lede }: { open?: boolean; lede?: string }) {
  const limits = useQuery({ queryKey: ["limits"], queryFn: () => unwrap(api.GET("/api/limits")) });
  // Both lists are read defensively. A truthy response whose arrays are absent used to throw inside the render
  // and blank the whole page -- survivable when this lived on Report behind a collapsed <details>, not when it
  // is the first thing Methods draws.
  const measured = limits.data?.measured ?? [];
  const unmeasured = limits.data?.unmeasured ?? [];
  if (!limits.data || (measured.length === 0 && unmeasured.length === 0)) return null;
  return (
    <section aria-label="What this result cannot tell you" className="limits">
      <details open={open || undefined}>
        <summary>
          <h2 style={{ display: "inline" }}>What this result cannot tell you</h2>{" "}
          <span className="lede">
            {measured.length} measured {measured.length === 1 ? "limit" : "limits"}
            {unmeasured.length ? `, ${unmeasured.length} not yet measured` : ""}
          </span>
        </summary>
        <p className="lede">
          {lede ?? "Measured limits of the funnel (docs/limits.md). Each rests on one system or one model pair."}
        </p>
        <table className="grid">
          <thead>
            <tr>
              <th>ID</th>
              <th>Limit</th>
              <th>Magnitude</th>
              <th>Claim it forbids</th>
            </tr>
          </thead>
          <tbody>
            {measured.map((l) => (
              <tr key={l.id}>
                <td>{l.id}</td>
                <td>{l.limit}</td>
                <td>{l.magnitude}</td>
                <td>{l.forbids}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <h3>Not yet measured</h3>
        <table className="grid">
          <thead>
            <tr>
              <th>ID</th>
              <th>Limit</th>
              <th>Status</th>
              <th>Claim it forbids until measured</th>
            </tr>
          </thead>
          <tbody>
            {unmeasured.map((l) => (
              <tr key={l.id}>
                <td>{l.id}</td>
                <td>{l.limit}</td>
                <td>{l.status}</td>
                <td>{l.forbids}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </section>
  );
}
