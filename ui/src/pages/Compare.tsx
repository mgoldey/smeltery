import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, unwrap } from "../api/client";
import { PlotlyFigure } from "../components/PlotlyFigure";
import { useScheme } from "../styles/scheme";
import { Shell } from "../components/Shell";

/** Two runs as arms, differenced per key. Arms are chosen, never inferred: guessing is how a comparison becomes a
 * pooling of unlike things. */
export function Compare() {
  const runs = useQuery({ queryKey: ["runs"], queryFn: () => unwrap(api.GET("/api/runs")) });
  const [a, setA] = useState<number | null>(null);
  const [b, setB] = useState<number | null>(null);
  const theme = useScheme();
  const cmp = useQuery({
    queryKey: ["compare", a, b, theme],
    queryFn: () => unwrap(api.GET("/api/compare", { params: { query: { a: a!, b: b!, theme } } })),
    enabled: a !== null && b !== null,
  });
  const usable = (runs.data ?? []).filter((r) => r.verdict && r.verdict.state !== "INCOMPLETE");
  const pick = (set: (v: number | null) => void) => (v: string) => set(v === "" ? null : Number(v));
  return (
    <Shell>
      <div className="body">
        <main className="work">
          <h1>Compare two runs</h1>
          <p className="lede">
            Choose the two arms. Each analogue's paired ΔΔE is compared against its own run's parent, and the difference
            between arms is judged against the larger of their floors.
          </p>
          <div className="controls">
            {(["A", "B"] as const).map((arm) => (
              <div key={arm}>
                <label htmlFor={`arm-${arm}`}>Arm {arm}</label>
                <select id={`arm-${arm}`} value={(arm === "A" ? a : b) ?? ""} onChange={(e) => pick(arm === "A" ? setA : setB)(e.target.value)}>
                  <option value="">choose a run</option>
                  {usable.map((r) => (
                    <option key={r.idx} value={r.idx}>
                      {r.campaign} {r.digest?.slice(0, 12)} ({r.verdict?.state.toLowerCase()})
                    </option>
                  ))}
                </select>
              </div>
            ))}
          </div>
          {cmp.isError ? (
            <div className="banner" role="alert">
              {cmp.error.message}
            </div>
          ) : null}
          {cmp.data ? (
            <>
              <p className="verdict-detail" data-testid="compare-caption">
                {cmp.data.caption}
              </p>
              <PlotlyFigure figure={cmp.data.figure} name="compare" />
              <table data-testid="compare-rows">
                <thead>
                  <tr>
                    <th>Key</th>
                    <th>Arm A median ({cmp.data.unit})</th>
                    <th>Arm B median ({cmp.data.unit})</th>
                    <th>A − B ({cmp.data.unit})</th>
                    <th>SEM of difference ({cmp.data.unit})</th>
                    <th>Resolved</th>
                  </tr>
                </thead>
                <tbody>
                  {cmp.data.rows.map((r) => (
                    <tr key={r.key}>
                      <td>{r.key}</td>
                      <td>{r.a === null ? "not run" : `${r.a.toFixed(2)} (n=${r.n_a})`}</td>
                      <td>{r.b === null ? "not run" : `${r.b.toFixed(2)} (n=${r.n_b})`}</td>
                      <td>{r.diff === null ? "—" : r.diff.toFixed(2)}</td>
                      <td>{r.sem === null ? "—" : r.sem.toFixed(2)}</td>
                      <td>{r.resolved === null ? "—" : r.resolved ? "yes" : "no"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <h2>Same candidates in both runs</h2>
              {cmp.data.candidates === null ? (
                <p data-testid="compare-candidates-note">{cmp.data.candidates_note}</p>
              ) : (
                <table data-testid="compare-candidates">
                  <thead>
                    <tr>
                      <th>Candidate</th>
                      <th>ΔΔE A ({cmp.data.unit})</th>
                      <th>ΔΔE B ({cmp.data.unit})</th>
                      <th>A − B ({cmp.data.unit})</th>
                      <th>SEM of difference ({cmp.data.unit})</th>
                      <th>Resolved</th>
                    </tr>
                  </thead>
                  <tbody>
                    {cmp.data.candidates.map((c) => (
                      <tr key={c.candidate}>
                        <td>{c.candidate}</td>
                        <td>{c.ddE_a === null ? "not run" : `${c.ddE_a.toFixed(2)} ± ${c.sem_a?.toFixed(2)}`}</td>
                        <td>{c.ddE_b === null ? "not run" : `${c.ddE_b.toFixed(2)} ± ${c.sem_b?.toFixed(2)}`}</td>
                        <td>{c.diff === null ? "—" : c.diff.toFixed(2)}</td>
                        <td>{c.diff_sem === null ? "—" : c.diff_sem.toFixed(2)}</td>
                        <td>{c.resolved === null ? "—" : c.resolved ? "yes" : "no"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </>
          ) : null}
        </main>
      </div>
    </Shell>
  );
}
