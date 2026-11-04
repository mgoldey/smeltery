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
            </>
          ) : null}
        </main>
      </div>
    </Shell>
  );
}
