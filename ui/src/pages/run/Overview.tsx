import { useQuery } from "@tanstack/react-query";
import { api, unwrap } from "../../api/client";
import { useRunIdx } from "./RunLayout";

export function Overview() {
  const idx = useRunIdx();
  const detail = useQuery({
    queryKey: ["run", idx],
    queryFn: () => unwrap(api.GET("/api/runs/{idx}", { params: { path: { idx } } })),
  });
  const verdict = useQuery({
    queryKey: ["verdict", idx],
    queryFn: () => unwrap(api.GET("/api/runs/{idx}/verdict", { params: { path: { idx } } })),
  });
  const d = detail.data;
  return (
    <div className="body">
      <main className="work">
        <h1>Overview</h1>
        {detail.isError ? (
          <div className="banner" role="alert">
            {verdict.data?.state === "INCOMPLETE"
              ? `This run is incomplete: its record has no ${verdict.data.missing.join(", ")}. It stopped before the cut, so there is no result to show.`
              : `This run cannot be read: ${detail.error.message}`}
          </div>
        ) : null}
        {d ? (
          <>
            <p className="lede">
              {d.analogues.length} analogues of <strong>{d.parent}</strong>, cut at keep {d.keep} with z = {d.z}. The floor is{" "}
              <span className="num">
                {d.floor} {d.unit}
              </span>{" "}
              ({d.floor_source}).
            </p>
            <h2>Candidates</h2>
            <table className="grid">
              <thead>
                <tr>
                  <th>Candidate</th>
                  <th>SMILES</th>
                  <th className="num">Poses</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(d.candidates).map(([name, smiles]) => (
                  <tr key={name}>
                    <td>
                      {name}
                      {name === d.parent ? <span className="lede"> (parent)</span> : null}
                    </td>
                    <td className="mono">{smiles}</td>
                    <td className="num">{d.n_poses[name] ?? "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <h2>Record</h2>
            <p className="mono">{d.path}</p>
            <p className="mono">input digest {d.digest}</p>
          </>
        ) : null}
      </main>
    </div>
  );
}
