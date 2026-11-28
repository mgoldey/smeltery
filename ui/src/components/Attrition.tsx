import type { components } from "../api/schema";

export type AttritionData = components["schemas"]["Attrition"];
type Generation = NonNullable<AttritionData["generation"]>;

/** Reasons that lost at least one molecule, most first; the closed reasons with a zero count are not worth a row. */
export function lostByReason(g: Generation) {
  return g.by_reason.filter((r) => r.count > 0).sort((a, b) => b.count - a.count || a.reason.localeCompare(b.reason));
}

/** "6 of 10 raw outputs were rejected" for a generation step; the first thing a reader needs to know. */
export function lostSentence(g: Generation): string {
  const lost = g.n_raw - g.n_returned;
  return `${lost} of ${g.n_raw} raw outputs were rejected; ${g.n_returned} reached the funnel.`;
}

/**
 * What the generator lost before the funnel saw anything (`results.funnel`'s `generate` row). Nothing for a run with
 * no generation step: gates stop a run rather than drop candidates, so its funnel is flat and has nothing to show here.
 */
export function Attrition({ data }: { data: AttritionData | undefined }) {
  const g = data?.generation;
  if (!g) return null;
  const rows = lostByReason(g);
  return (
    <section aria-label="Attrition" data-testid="attrition">
      <h2>Where candidates were lost</h2>
      <p className="lede">{lostSentence(g)}</p>
      {rows.length ? (
        <table className="grid" aria-label="Rejections by reason">
          <thead>
            <tr>
              <th>Reason</th>
              <th className="num">Rejected</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.reason}>
                <td>{r.reason}</td>
                <td className="num">{r.count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {g.rejected.length ? (
        <details>
          <summary>
            Rejected molecules ({g.rejected_listed} of {g.rejected_total} listed)
          </summary>
          <ul>
            {g.rejected.map((r) => (
              <li key={r.index}>
                #{r.index} {r.reason}
                {r.detail ? ` (${r.detail})` : ""}: <code>{r.smiles ?? "no SMILES"}</code>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </section>
  );
}
