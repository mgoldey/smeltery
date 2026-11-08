import { useQuery } from "@tanstack/react-query";
import { api, unwrap } from "../../api/client";
import { EmptyState } from "../../components/EmptyState";
import { Structure2D } from "../../components/Structure2D";
import { useRunIdx } from "./RunLayout";

/** The computational chemist's review queue: what bench chemists proposed after reading this run's report. */
export function Proposals() {
  const idx = useRunIdx();
  const verdict = useQuery({ queryKey: ["runs"], queryFn: () => unwrap(api.GET("/api/runs")) });
  const digest = verdict.data?.find((r) => r.idx === idx)?.digest ?? undefined;
  const proposals = useQuery({
    queryKey: ["proposals", digest],
    queryFn: () => unwrap(api.GET("/api/proposals", { params: { query: { run: digest ?? "" } } })),
    enabled: digest !== undefined,
  });
  return (
    <div className="body">
      <main className="work">
        <h1>Proposals</h1>
        <p className="lede">Candidates proposed from this run's report. Add one to the next pipeline's candidates if you agree.</p>
        {proposals.data && proposals.data.length === 0 ? <EmptyState action={{ label: "Open the report", to: `/report/${idx}` }}>
            Nothing has been proposed from this run yet. Bench chemists propose candidates after reading its report.
          </EmptyState> : null}
        {proposals.data && proposals.data.length ? (
          <table className="grid" aria-label="Review queue">
            <thead>
              <tr>
                <th>Structure</th>
                <th>SMILES</th>
                <th>Reason</th>
                <th>Note</th>
                <th>From</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {proposals.data.map((p) => (
                <tr key={p.id} data-proposal={p.smiles}>
                  <td>
                    <Structure2D smiles={p.smiles} width={120} height={80} />
                  </td>
                  <td className="mono">{p.smiles}</td>
                  <td>{p.reason}</td>
                  <td>{p.note}</td>
                  <td>{p.author}</td>
                  <td>{p.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </main>
    </div>
  );
}
