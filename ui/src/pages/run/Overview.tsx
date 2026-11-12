import { useQuery } from "@tanstack/react-query";
import { api, unwrap } from "../../api/client";
import { DroppedPoses } from "../../components/DroppedPoses";
import { useRunId } from "./RunLayout";

export function Overview() {
  const runId = useRunId();
  const detail = useQuery({
    queryKey: ["run", runId],
    queryFn: () => unwrap(api.GET("/api/runs/{run_id}", { params: { path: { run_id: runId } } })),
  });
  const verdict = useQuery({
    queryKey: ["verdict", runId],
    queryFn: () => unwrap(api.GET("/api/runs/{run_id}/verdict", { params: { path: { run_id: runId } } })),
  });
  const drops = useQuery({
    queryKey: ["dropped_poses", runId],
    queryFn: () => unwrap(api.GET("/api/runs/{run_id}/dropped_poses", { params: { path: { run_id: runId } } })),
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
            <DroppedPoses dropped={drops.data?.dropped} />
            <h2>Record</h2>
            <p className="mono">{d.path}</p>
            <p className="mono">input digest {d.digest}</p>
          </>
        ) : null}
      </main>
    </div>
  );
}
