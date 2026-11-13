import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { api, unwrap } from "../api/client";
import { PlotlyFigure } from "../components/PlotlyFigure";
import { EmptyState } from "../components/EmptyState";
import { useScheme } from "../styles/scheme";
import { Shell } from "../components/Shell";

const withSem = (v: number, sem: number | null) => (sem === null ? v.toFixed(2) : `${v.toFixed(2)} ± ${sem.toFixed(2)}`);

/** Two runs as arms, differenced per key. Arms are chosen, never inferred: guessing is how a comparison becomes a
 * pooling of unlike things. */
export function Compare() {
  const runs = useQuery({ queryKey: ["runs"], queryFn: () => unwrap(api.GET("/api/runs")) });
  // The arms live in the URL, not in component state. That is what lets a run link into a comparison already
  // holding it ("Compare with another run"), and what makes a comparison worth sending to a colleague.
  const [params, setParams] = useSearchParams();
  const a = params.get("a");
  const b = params.get("b");
  const setArm = (arm: "a" | "b") => (v: string | null) => {
    const next = new URLSearchParams(params);
    if (v === null) next.delete(arm);
    else next.set(arm, v);
    setParams(next, { replace: true });
  };
  const setA = setArm("a");
  const setB = setArm("b");
  const theme = useScheme();
  const cmp = useQuery({
    queryKey: ["compare", a, b, theme],
    queryFn: () => unwrap(api.GET("/api/compare", { params: { query: { a: a!, b: b!, theme } } })),
    enabled: a !== null && b !== null,
  });
  const usable = (runs.data ?? []).filter((r) => r.verdict && r.verdict.state !== "INCOMPLETE");
  const pick = (set: (v: string | null) => void) => (v: string) => set(v === "" ? null : v);
  return (
    <Shell>
      <div className="body">
        <main className="work">
          <h1>Compare two runs</h1>
          <p className="lede">
            Choose the two arms. Each analogue's paired ΔΔE is compared against its own run's parent, and the difference
            between arms is judged against the larger of their floors.
          </p>
          {runs.data && usable.length < 2 ? (
            <EmptyState action={{ label: "Go to Bench", to: "/bench" }}>
              Comparing needs at least two finished runs; {usable.length === 0 ? "none is" : "only one is"} ready. Run a pipeline
              to completion, then come back.
            </EmptyState>
          ) : null}
          <div className="controls">
            {(["A", "B"] as const).map((arm) => (
              <div key={arm}>
                <label htmlFor={`arm-${arm}`}>Arm {arm}</label>
                <select id={`arm-${arm}`} value={(arm === "A" ? a : b) ?? ""} onChange={(e) => pick(arm === "A" ? setA : setB)(e.target.value)}>
                  <option value="">choose a run</option>
                  {usable.map((r) => (
                    <option key={r.id} value={r.id}>
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
              {/* A comparison is a reading of two runs; it should never be the end of the road. Each arm links
                  back to the run it is, so a difference worth chasing can be chased. */}
              <p className="next-step" data-testid="compare-arms">
                <Link className="button" to={`/run/${a}/results`} data-testid="open-arm-a">
                  Open arm A
                </Link>{" "}
                <Link className="button" to={`/run/${b}/results`} data-testid="open-arm-b">
                  Open arm B
                </Link>{" "}
                <span className="lede">Each arm's own result, with its poses, decisions and provenance.</span>
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
              <h2>Per-candidate ΔΔE (same parent)</h2>
              {cmp.data.candidates_note ? <p data-testid="compare-candidates-note">{cmp.data.candidates_note}</p> : null}
              {cmp.data.candidates === null ? null : (
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
                        <td>{c.ddE_a === null ? "not run" : withSem(c.ddE_a, c.sem_a)}</td>
                        <td>{c.ddE_b === null ? "not run" : withSem(c.ddE_b, c.sem_b)}</td>
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
