import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, unwrap } from "../../api/client";
import { MolstarViewer } from "../../components/MolstarViewer";
import { useRunIdx } from "./RunLayout";

export function Poses() {
  const idx = useRunIdx();
  const detail = useQuery({
    queryKey: ["run", idx],
    queryFn: () => unwrap(api.GET("/api/runs/{idx}", { params: { path: { idx } } })),
  });
  const d = detail.data;
  const [candidate, setCandidate] = useState<string | null>(null);
  const [pose, setPose] = useState(0);
  const chosen = candidate ?? d?.analogues[0] ?? null;
  const nPoses = chosen && d ? (d.n_poses[chosen] ?? 0) : 0;
  const scene = useQuery({
    queryKey: ["scene", idx, chosen, pose],
    queryFn: () =>
      unwrap(api.GET("/api/runs/{idx}/scene", { params: { path: { idx }, query: { candidate: chosen ?? "", pose } } })),
    enabled: Boolean(d?.has_pose_output && chosen),
  });

  return (
    <div className="body">
      <main className="work">
        <h1>Poses</h1>
        {detail.isError ? (
          <div className="banner" role="alert">
            {detail.error.message}
          </div>
        ) : null}
        {d && !d.has_pose_output ? (
          <div className="banner" role="alert">
            This run record predates pose output, so it has no poses to show. Re-run it with this version of smeltery to
            see them.
          </div>
        ) : null}
        {d && d.has_pose_output && chosen ? (
          <>
            <div className="controls">
              <div>
                <label htmlFor="pose-candidate">Candidate</label>
                <select
                  id="pose-candidate"
                  value={chosen}
                  onChange={(e) => {
                    setCandidate(e.target.value);
                    setPose(0);
                  }}
                >
                  {d.analogues.map((a) => (
                    <option key={a} value={a}>
                      {a}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label htmlFor="pose-index">Pose</label>
                <select id="pose-index" value={pose} onChange={(e) => setPose(Number(e.target.value))}>
                  {Array.from({ length: nPoses }, (_, i) => (
                    <option key={i} value={i}>
                      {i}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            <p className="pairing" data-testid="pairing">
              Pose {pose} of {chosen} is paired to pose {pose} of {d.parent}, shown muted. Charges are drawn as spheres:
              positive and negative by colour.
            </p>
            {scene.isError ? (
              <div className="banner" role="alert">
                {scene.error.message}
              </div>
            ) : null}
            <MolstarViewer state={scene.data} label="pose viewer" />
          </>
        ) : null}
      </main>
    </div>
  );
}
